#define _GNU_SOURCE

#include "bash_readline_abi.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <locale.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <wchar.h>

#define GHOST_MAX_LINE 32768
#define GHOST_TIMEOUT_MS 8

static const char *const end_key_sequences[] = {
  "\033OF", "\033[4~", "\033[F", "\033[8~"
};

static rl_voidfunc_t *original_redisplay;
static rl_command_func_t *original_right;
static rl_hook_func_t *original_startup_hook;
static int daemon_fd = -1;
static pid_t daemon_pid = -1;
static char *suggestion;
static char *cached_line;
static char *last_submitted_line;
static char *configured_histfile;
static unsigned short last_terminal_rows;
static unsigned short last_terminal_columns;
static int ghost_visible;
static int installed;
static int readline_initialized;

extern char **environ;

static int start_daemon(void);
static void suspend_predictions(void);

static int predictions_enabled(void)
{
  const char *setting = get_string_value("ANDUINOS_GUESS_COMMAND");
  return setting == NULL || strcmp(setting, "0") != 0;
}

static void terminal_write(const char *value, size_t length)
{
  ssize_t ignored = write(STDOUT_FILENO, value, length);
  (void)ignored;
}

static void clear_suggestion(void)
{
  free(suggestion);
  suggestion = NULL;
}

static uint64_t now_ms(void)
{
  struct timespec ts;
  if (clock_gettime(CLOCK_REALTIME, &ts) != 0)
    return 0;
  return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static uint64_t monotonic_ms(void)
{
  struct timespec ts;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return 0;
  return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static void reap_daemon(pid_t pid, int force)
{
  if (pid > 0) {
    /* The helper is private to this Bash process. A blocking reap after
       SIGKILL is preferable to losing its pid after a one-shot WNOHANG and
       accumulating zombies when a helper repeatedly misses its deadline. */
    if (!force || kill(pid, SIGKILL) == 0 || errno == ESRCH) {
      while (waitpid(pid, NULL, 0) < 0 && errno == EINTR)
        ;
    }
  }
}

static void force_stop_daemon(void)
{
  pid_t pid = daemon_pid;

  if (daemon_fd >= 0)
    close(daemon_fd);
  daemon_fd = -1;
  daemon_pid = -1;
  reap_daemon(pid, 1);
}

static void graceful_stop_daemon(void)
{
  static const char quit[] = "X\n";
  struct pollfd descriptor;
  uint64_t deadline;
  pid_t pid = daemon_pid;
  int fd = daemon_fd;
  size_t sent = 0;
  int exited = 0;

  daemon_fd = -1;
  daemon_pid = -1;
  if (fd < 0) {
    reap_daemon(pid, 1);
    return;
  }
  deadline = monotonic_ms() + 200;
  descriptor.fd = fd;
  while (sent < sizeof(quit) - 1) {
    ssize_t count = send(fd, quit + sent, sizeof(quit) - 1 - sent, MSG_NOSIGNAL);
    if (count > 0) {
      sent += (size_t)count;
      continue;
    }
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
      uint64_t current = monotonic_ms();
      int remaining = current < deadline ? (int)(deadline - current) : 0;
      descriptor.events = POLLOUT;
      if (remaining > 0 && poll(&descriptor, 1, remaining) > 0)
        continue;
    }
    break;
  }
  if (sent == sizeof(quit) - 1) {
    char response[16];
    while (monotonic_ms() < deadline) {
      int status;
      pid_t waited = waitpid(pid, &status, WNOHANG);
      if (waited == pid || (waited < 0 && errno == ECHILD)) {
        exited = 1;
        break;
      }
      descriptor.events = POLLIN;
      (void)poll(&descriptor, 1, 5);
      (void)recv(fd, response, sizeof(response), MSG_DONTWAIT);
    }
  }
  close(fd);
  if (!exited)
    reap_daemon(pid, 1);
}

static void close_inherited_fds(void)
{
  DIR *directory;
  struct dirent *entry;
  int directory_fd;

#if defined(__linux__)
  if (close_range(3, ~0U, 0) == 0)
    return;
#endif

  /* /proc is mounted on supported AnduinOS systems. Keep a conservative
     fallback for restricted containers where close_range is unavailable. */
  directory = opendir("/proc/self/fd");
  if (directory != NULL) {
    directory_fd = dirfd(directory);
    while ((entry = readdir(directory)) != NULL) {
      char *end = NULL;
      long descriptor = strtol(entry->d_name, &end, 10);
      if (end != entry->d_name && *end == '\0' && descriptor >= 3 &&
          descriptor != directory_fd)
        close((int)descriptor);
    }
    closedir(directory);
    return;
  }

  for (int descriptor = 3; descriptor < 1024; ++descriptor)
    close(descriptor);
}

static int environment_name_is(const char *entry, const char *name)
{
  size_t length = strlen(name);
  return strncmp(entry, name, length) == 0 && entry[length] == '=';
}

static char *environment_entry(const char *name, const char *value)
{
  char *entry = NULL;
  if (value != NULL && asprintf(&entry, "%s=%s", name, value) < 0)
    return NULL;
  return entry;
}

static char **daemon_environment(const char *path, const char *histfile,
                                 const char *history_setting,
                                 const char *persist_setting,
                                 char **owned, size_t owned_capacity)
{
  size_t source_count = 0, target_count = 0, owned_count = 0;
  char **result;

  while (environ != NULL && environ[source_count] != NULL)
    ++source_count;
  result = calloc(source_count + owned_capacity + 1, sizeof(*result));
  if (result == NULL)
    return NULL;
  for (size_t index = 0; index < source_count; ++index) {
    const char *entry = environ[index];
    if (environment_name_is(entry, "PATH") ||
        environment_name_is(entry, "ANDUINOS_BASH_HISTFILE") ||
        environment_name_is(entry, "ANDUINOS_GUESS_COMMAND") ||
        environment_name_is(entry, "ANDUINOS_GUESS_HISTORY") ||
        environment_name_is(entry, "ANDUINOS_GUESS_PERSIST"))
      continue;
    result[target_count++] = environ[index];
  }

#define ADD_ENVIRONMENT(name, value)                                           \
  do {                                                                          \
    if ((value) != NULL) {                                                       \
      char *entry = environment_entry((name), (value));                          \
      if (entry == NULL) {                                                       \
        for (size_t index = 0; index < owned_count; ++index)                     \
          free(owned[index]);                                                    \
        free(result);                                                            \
        return NULL;                                                             \
      }                                                                          \
      owned[owned_count++] = entry;                                              \
      result[target_count++] = entry;                                            \
    }                                                                            \
  } while (0)

  ADD_ENVIRONMENT("PATH", path);
  ADD_ENVIRONMENT("ANDUINOS_BASH_HISTFILE", histfile);
  ADD_ENVIRONMENT("ANDUINOS_GUESS_HISTORY", history_setting);
  ADD_ENVIRONMENT("ANDUINOS_GUESS_PERSIST", persist_setting);
#undef ADD_ENVIRONMENT
  result[target_count] = NULL;
  return result;
}

static void prewarm_fresh_daemon(void)
{
  force_stop_daemon();
  (void)start_daemon();
}

#if defined(__GNUC__) && !defined(__clang__)
/* GCC's analyzer treats descriptors intentionally installed as the child's
   stdin/stdout/stderr as leaks on the successful exec path. The helper must
   retain those three descriptors; close_inherited_fds() closes everything
   else, and the interactive test verifies that unrelated shell descriptors
   do not reach the helper. Keep this suppression local to the exec wrapper. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wanalyzer-fd-leak"
#endif
static int start_daemon(void)
{
  int sockets[2];
  pid_t child;
  char *owned_environment[4] = {0};
  char **child_environment;
  const char *binary, *shell_path, *shell_histfile;
  const char *history_setting, *persist_setting;

  if (!predictions_enabled())
    return -1;
  if (daemon_fd >= 0)
    return 0;
  binary = get_string_value("ANDUINOS_QUIETD");
  if (binary == NULL || *binary == '\0')
    binary = "/usr/lib/anduinos-bash-guess-command/anduinos-quietd";
  shell_path = get_string_value("PATH");
  shell_histfile = configured_histfile;
  history_setting = get_string_value("ANDUINOS_GUESS_HISTORY");
  persist_setting = get_string_value("ANDUINOS_GUESS_PERSIST");
  child_environment = daemon_environment(
      shell_path, shell_histfile, history_setting, persist_setting,
      owned_environment, sizeof(owned_environment) / sizeof(owned_environment[0]));
  if (child_environment == NULL)
    return -1;
  if (access(binary, X_OK) != 0 ||
      socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) != 0) {
    for (size_t index = 0; index < 4; ++index)
      free(owned_environment[index]);
    free(child_environment);
    return -1;
  }

  child = fork();
  if (child < 0) {
    close(sockets[0]);
    close(sockets[1]);
    for (size_t index = 0; index < 4; ++index)
      free(owned_environment[index]);
    free(child_environment);
    return -1;
  }
  if (child == 0) {
    int nullfd;
    close(sockets[0]);
    if (dup2(sockets[1], STDIN_FILENO) < 0) {
      close(sockets[1]);
      _exit(127);
    }
    if (dup2(sockets[1], STDOUT_FILENO) < 0) {
      close(sockets[1]);
      close(STDIN_FILENO);
      _exit(127);
    }
    nullfd = open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (nullfd < 0) {
      close(sockets[1]);
      close(STDIN_FILENO);
      close(STDOUT_FILENO);
      _exit(127);
    }
    if (dup2(nullfd, STDERR_FILENO) < 0) {
      close(nullfd);
      close(sockets[1]);
      close(STDIN_FILENO);
      close(STDOUT_FILENO);
      _exit(127);
    }
    close_inherited_fds();
    {
      char *const arguments[] = {(char *)binary, NULL};
      execve(binary, arguments, child_environment);
    }
    _exit(127);
  }

  close(sockets[1]);
  for (size_t index = 0; index < 4; ++index)
    free(owned_environment[index]);
  free(child_environment);
  {
    int flags = fcntl(sockets[0], F_GETFL, 0);
    if (flags < 0 || fcntl(sockets[0], F_SETFL, flags | O_NONBLOCK) != 0) {
      close(sockets[0]);
      kill(child, SIGKILL);
      while (waitpid(child, NULL, 0) < 0 && errno == EINTR)
        ;
      return -1;
    }
  }
  daemon_fd = sockets[0];
  daemon_pid = child;
  return 0;
}
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC diagnostic pop
#endif

static char hex_digit(unsigned value)
{
  return "0123456789abcdef"[value & 15u];
}

static char *hex_encode(const char *value)
{
  size_t length = strlen(value);
  char *encoded;
  size_t index;
  if (length > (GHOST_MAX_LINE - 64) / 2)
    return NULL;
  encoded = malloc(length * 2 + 1);
  if (encoded == NULL)
    return NULL;
  for (index = 0; index < length; ++index) {
    unsigned char byte = (unsigned char)value[index];
    encoded[index * 2] = hex_digit(byte >> 4);
    encoded[index * 2 + 1] = hex_digit(byte);
  }
  encoded[length * 2] = '\0';
  return encoded;
}

static int nibble(char value)
{
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

static char *hex_decode(const char *value)
{
  size_t length = strlen(value), index;
  char *decoded;
  if ((length & 1u) != 0)
    return NULL;
  decoded = malloc(length / 2 + 1);
  if (decoded == NULL)
    return NULL;
  for (index = 0; index < length; index += 2) {
    int high = nibble(value[index]);
    int low = nibble(value[index + 1]);
    if (high < 0 || low < 0) {
      free(decoded);
      return NULL;
    }
    decoded[index / 2] = (char)((high << 4) | low);
  }
  decoded[length / 2] = '\0';
  return decoded;
}

static int exchange(const char *request, char *response, size_t capacity)
{
  struct pollfd descriptor;
  uint64_t deadline;
  size_t sent = 0, used = 0, length = strlen(request);
  ssize_t count;

  if (start_daemon() != 0)
    return -1;
  deadline = monotonic_ms() + GHOST_TIMEOUT_MS;
  descriptor.fd = daemon_fd;
  while (sent < length) {
    count = send(daemon_fd, request + sent, length - sent, MSG_NOSIGNAL);
    if (count > 0) {
      sent += (size_t)count;
      continue;
    }
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
      uint64_t current = monotonic_ms();
      int remaining = current < deadline ? (int)(deadline - current) : 0;
      descriptor.events = POLLOUT;
      if (remaining > 0 && poll(&descriptor, 1, remaining) > 0)
        continue;
    }
    {
      /* Return silence for this key, but immediately leave a fresh helper
         warming in the background. The following key must not cold-start a
         process under the normal 8 ms query deadline. */
      prewarm_fresh_daemon();
      return -1;
    }
  }

  descriptor.events = POLLIN;
  while (used + 1 < capacity) {
    uint64_t current = monotonic_ms();
    int remaining = current < deadline ? (int)(deadline - current) : 0;
    if (remaining <= 0 || poll(&descriptor, 1, remaining) <= 0) {
      prewarm_fresh_daemon();
      return -1;
    }
    count = recv(daemon_fd, response + used, capacity - used - 1, 0);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
      continue;
    if (count <= 0) {
      prewarm_fresh_daemon();
      return -1;
    }
    {
      char *newline = memchr(response + used, '\n', (size_t)count);
      used += (size_t)count;
      if (newline != NULL) {
        newline[1] = '\0';
        return 0;
      }
    }
    if (used + 1 >= capacity) {
      prewarm_fresh_daemon();
      return -1;
    }
  }
  prewarm_fresh_daemon();
  return -1;
}

static int format_request(char *buffer, size_t capacity, const char *format,
                          unsigned long long timestamp, const char *first,
                          const char *second)
{
  int written;
  if (second == NULL)
    written = snprintf(buffer, capacity, format, timestamp, first);
  else
    written = snprintf(buffer, capacity, format, timestamp, first, second);
  if (written < 0 || (size_t)written >= capacity)
    return -1;
  return 0;
}

static void query(const char *line)
{
  char request[GHOST_MAX_LINE];
  char response[GHOST_MAX_LINE];
  char *encoded, *field, *tab;

  clear_suggestion();
  encoded = hex_encode(line);
  if (encoded == NULL)
    return;
  if (format_request(request, sizeof(request), "Q\t%llu\t%s\n",
                     (unsigned long long)now_ms(), encoded, NULL) != 0) {
    free(encoded);
    return;
  }
  free(encoded);
  if (exchange(request, response, sizeof(response)) != 0 ||
      response[0] != 'S' || response[1] != '\t')
    return;
  field = response + 2;
  tab = strchr(field, '\t');
  if (tab == NULL)
    return;
  *tab = '\0';
  suggestion = hex_decode(field);
  if (suggestion != NULL &&
      (strpbrk(suggestion, "\r\n\t\177") != NULL || *suggestion == '\0'))
    clear_suggestion();
}

static int display_width_bytes(const char *text, size_t length)
{
  mbstate_t state = {0};
  const char *cursor = text;
  size_t remaining = length, consumed;
  int total = 0, width;
  wchar_t character;
  while (remaining > 0) {
    consumed = mbrtowc(&character, cursor, remaining, &state);
    if (consumed == (size_t)-1 || consumed == (size_t)-2)
      return -1;
    if (consumed == 0)
      break;
    width = wcwidth(character);
    if (width < 0)
      return -1;
    total += width;
    cursor += consumed;
    remaining -= consumed;
  }
  return total;
}

static int display_width(const char *text)
{
  return display_width_bytes(text, strlen(text));
}

static int fits_one_row(const char *line, int suffix_width)
{
  struct winsize terminal;
  int line_width;
  if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &terminal) != 0 || terminal.ws_col == 0)
    return 0;
  line_width = display_width(line);
  if (line_width < 0)
    return 0;
  return rl_visible_prompt_length + line_width + suffix_width < terminal.ws_col;
}

static void erase_ghost(void)
{
  if (ghost_visible) {
    static const char erase[] = "\033[K";
    terminal_write(erase, sizeof(erase) - 1);
    ghost_visible = 0;
  }
}

static void clear_resize_artifacts(void)
{
  struct winsize terminal;
  int line_width, cursor_width, rows_above;
  char movement[32];
  int movement_length;

  if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &terminal) != 0 ||
      terminal.ws_col == 0)
    return;
  if (last_terminal_columns == 0) {
    last_terminal_rows = terminal.ws_row;
    last_terminal_columns = terminal.ws_col;
    return;
  }
  if (last_terminal_rows == terminal.ws_row &&
      last_terminal_columns == terminal.ws_col)
    return;
  last_terminal_rows = terminal.ws_row;
  last_terminal_columns = terminal.ws_col;

  /* Readline treats any replacement rl_redisplay_function as a complete
     custom renderer. On SIGWINCH it resets its logical display state, but it
     does not clear the physical line before invoking us. Clear the reflowed
     prompt/input first so the delegated stock renderer does not append a new
     prompt after the old one. */
  line_width = rl_line_buffer != NULL && rl_point > 0
                   ? display_width_bytes(rl_line_buffer, (size_t)rl_point)
                   : 0;
  cursor_width = rl_visible_prompt_length + (line_width > 0 ? line_width : 0);
  rows_above = cursor_width > 0
                   ? (cursor_width - 1) / (int)terminal.ws_col
                   : 0;
  terminal_write("\r", 1);
  if (rows_above > 0) {
    movement_length =
        snprintf(movement, sizeof(movement), "\033[%dA", rows_above);
    if (movement_length > 0)
      terminal_write(movement, (size_t)movement_length);
  }
  terminal_write("\033[J", 3);
  ghost_visible = 0;
}

static void ghost_redisplay(void)
{
  int width;
  char movement[32];
  int movement_length;

  clear_resize_artifacts();
  erase_ghost();
  if (original_redisplay != NULL)
    original_redisplay();
  if (!predictions_enabled()) {
    suspend_predictions();
    return;
  }
  if (rl_line_buffer == NULL)
    return;

  free(last_submitted_line);
  last_submitted_line = strdup(rl_line_buffer);
  if (rl_point != rl_end || rl_end == 0 || strchr(rl_line_buffer, '\n') != NULL)
    return;
  if (cached_line == NULL || strcmp(cached_line, rl_line_buffer) != 0) {
    free(cached_line);
    cached_line = strdup(rl_line_buffer);
    query(rl_line_buffer);
  }
  if (suggestion == NULL)
    return;
  width = display_width(suggestion);
  if (width <= 0 || !fits_one_row(rl_line_buffer, width))
    return;
  terminal_write("\033[90m", 5);
  terminal_write(suggestion, strlen(suggestion));
  terminal_write("\033[0m", 4);
  movement_length = snprintf(movement, sizeof(movement), "\033[%dD", width);
  if (movement_length > 0)
    terminal_write(movement, (size_t)movement_length);
  ghost_visible = 1;
}

static int accept_ghost(int count, int key)
{
  (void)count;
  (void)key;
  if (!predictions_enabled()) {
    suspend_predictions();
    if (original_right != NULL)
      return original_right(count, key);
    return rl_forward_char(count, key);
  }
  if (rl_point == rl_end && suggestion != NULL && *suggestion != '\0') {
    erase_ghost();
    rl_insert_text(suggestion);
    free(cached_line);
    cached_line = NULL;
    clear_suggestion();
    return 0;
  }
  if (original_right != NULL)
    return original_right(count, key);
  return rl_forward_char(count, key);
}

static int accept_ghost_end(int count, int key)
{
  if (!predictions_enabled()) {
    suspend_predictions();
    return rl_end_of_line(count, key);
  }
  if (rl_point == rl_end && suggestion != NULL && *suggestion != '\0') {
    erase_ghost();
    rl_insert_text(suggestion);
    free(cached_line);
    cached_line = NULL;
    clear_suggestion();
    return 0;
  }
  return rl_end_of_line(count, key);
}

static void install_readline_hooks(void)
{
  int binding_type = 0;
  rl_command_func_t *current;

  /* Loading a Bash builtin from .bashrc happens before Readline prepares its
     first interactive line. Replacing rl_redisplay_function that early makes
     Readline treat the session as having a complete custom renderer and skips
     terminal features such as bracketed-paste negotiation. ghost_startup()
     is the sole transition that marks Readline ready; keep this guard so a
     future builtin subcommand cannot accidentally reintroduce that ordering
     bug. */
  if (!readline_initialized)
    return;
  if (original_redisplay == NULL && rl_redisplay_function != ghost_redisplay)
    original_redisplay = rl_redisplay_function;
  if (original_redisplay == NULL)
    return;
  if (rl_redisplay_function != ghost_redisplay)
    rl_redisplay_function = ghost_redisplay;
  current = rl_function_of_keyseq("\033[C", rl_get_keymap(), &binding_type);
  if (current != accept_ghost) {
    if (current != NULL)
      original_right = current;
    rl_bind_keyseq("\033[C", accept_ghost);
  }
  for (size_t index = 0;
       index < sizeof(end_key_sequences) / sizeof(end_key_sequences[0]);
       ++index) {
    current = rl_function_of_keyseq(end_key_sequences[index],
                                    rl_get_keymap(), &binding_type);
    /* Preserve user macros and custom End bindings. Only wrap sequences that
       currently have Readline's standard end-of-line behavior. */
    if (current == rl_end_of_line)
      rl_bind_keyseq(end_key_sequences[index], accept_ghost_end);
  }
}

static void suspend_predictions(void)
{
  int binding_type = 0;
  rl_command_func_t *current;

  erase_ghost();
  if (rl_redisplay_function == ghost_redisplay && original_redisplay != NULL)
    rl_redisplay_function = original_redisplay;
  current = rl_function_of_keyseq("\033[C", rl_get_keymap(), &binding_type);
  if (current == accept_ghost)
    rl_bind_keyseq("\033[C",
                   original_right != NULL ? original_right : rl_forward_char);
  for (size_t index = 0;
       index < sizeof(end_key_sequences) / sizeof(end_key_sequences[0]);
       ++index) {
    current = rl_function_of_keyseq(end_key_sequences[index],
                                    rl_get_keymap(), &binding_type);
    if (current == accept_ghost_end)
      rl_bind_keyseq(end_key_sequences[index], rl_end_of_line);
  }
  graceful_stop_daemon();
  clear_suggestion();
  free(cached_line);
  cached_line = NULL;
  free(last_submitted_line);
  last_submitted_line = NULL;
}

static int ghost_startup(void)
{
  int result = 0;
  if (original_startup_hook != NULL)
    result = original_startup_hook();

  /* Readline has now parsed inputrc and prepared its terminal modes. Install
     the narrow renderer/key wrappers only after preserving that native state. */
  readline_initialized = 1;
  if (predictions_enabled()) {
    install_readline_hooks();
    (void)start_daemon();
  } else {
    suspend_predictions();
  }
  return result;
}

static int observe(int status, const char *cwd)
{
  char request[GHOST_MAX_LINE], response[64];
  char *line_hex, *cwd_hex;
  int result = -1;
  if (last_submitted_line == NULL || *last_submitted_line == '\0')
    return 0;
  line_hex = hex_encode(last_submitted_line);
  cwd_hex = hex_encode(cwd == NULL ? "" : cwd);
  if (line_hex != NULL && cwd_hex != NULL) {
    int written = snprintf(request, sizeof(request), "O\t%d\t%llu\t%s\t%s\n",
                           status, (unsigned long long)now_ms(), line_hex, cwd_hex);
    if (written >= 0 && (size_t)written < sizeof(request))
      result = exchange(request, response, sizeof(response));
  }
  free(line_hex);
  free(cwd_hex);
  free(last_submitted_line);
  last_submitted_line = NULL;
  return result;
}

static void diagnose(const char *line)
{
  struct winsize terminal = {0};
  int binding_type = -1;
  rl_command_func_t *right =
      rl_function_of_keyseq("\033[C", rl_get_keymap(), &binding_type);
  int columns = ioctl(STDOUT_FILENO, TIOCGWINSZ, &terminal) == 0
                    ? (int)terminal.ws_col
                    : 0;

  printf("enabled=%d installed=%d daemon_pid=%ld daemon_fd=%d\n",
         predictions_enabled(), installed, (long)daemon_pid, daemon_fd);
  printf("redisplay_hook=%d startup_hook=%d right_hook=%d right_type=%d "
         "prompt_width=%d columns=%d\n",
         rl_redisplay_function == ghost_redisplay,
         rl_startup_hook == ghost_startup,
         right == accept_ghost, binding_type,
         rl_visible_prompt_length, columns);
  printf("HISTFILE=%s configured_histfile=%s history=%s engine=%s\n",
         get_string_value("HISTFILE") != NULL
             ? get_string_value("HISTFILE")
             : "<unset>",
         configured_histfile != NULL ? configured_histfile : "<unset>",
         get_string_value("ANDUINOS_GUESS_HISTORY") != NULL
             ? get_string_value("ANDUINOS_GUESS_HISTORY")
             : "<default>",
         get_string_value("ANDUINOS_QUIETD") != NULL
             ? get_string_value("ANDUINOS_QUIETD")
             : "<default>");
  if (line == NULL || *line == '\0')
    return;
  query(line);
  printf("query=%s suggestion=%s fits=%d daemon_pid=%ld daemon_fd=%d\n",
         line, suggestion != NULL ? suggestion : "<none>",
         suggestion != NULL
             ? fits_one_row(line, display_width(suggestion))
             : 0,
         (long)daemon_pid, daemon_fd);
}

int anduinos_ghost_builtin(WORD_LIST *list)
{
  if (!predictions_enabled()) {
    suspend_predictions();
    return EXECUTION_SUCCESS;
  }
  if (list != NULL && strcmp(list->word->word, "configure-history") == 0) {
    const char *value = list->next != NULL ? list->next->word->word : "";
    char *copy = *value != '\0' ? strdup(value) : NULL;
    if (*value == '\0' || copy != NULL) {
      int changed =
          (configured_histfile == NULL) != (copy == NULL) ||
          (configured_histfile != NULL && copy != NULL &&
           strcmp(configured_histfile, copy) != 0);
      free(configured_histfile);
      configured_histfile = copy;
      if (changed && daemon_fd >= 0)
        force_stop_daemon();
    }
    (void)start_daemon();
    return EXECUTION_SUCCESS;
  }
  if (list != NULL && strcmp(list->word->word, "diagnose") == 0) {
    list = list->next;
    diagnose(list != NULL ? list->word->word : NULL);
    return EXECUTION_SUCCESS;
  }
  if (list != NULL && strcmp(list->word->word, "observe") == 0) {
    int status = 0;
    const char *cwd = "";
    list = list->next;
    if (list != NULL) {
      status = atoi(list->word->word);
      list = list->next;
    }
    if (list != NULL)
      cwd = list->word->word;
    (void)observe(status, cwd);
  }
  return EXECUTION_SUCCESS;
}

int anduinos_ghost_builtin_load(char *name)
{
  (void)name;
  if (installed)
    return 1;
  setlocale(LC_CTYPE, "");
  original_startup_hook = rl_startup_hook;
  rl_startup_hook = ghost_startup;
  installed = 1;
  return 1;
}

void anduinos_ghost_builtin_unload(char *name)
{
  (void)name;
  if (!installed)
    return;
  erase_ghost();
  if (original_redisplay != NULL)
    rl_redisplay_function = original_redisplay;
  rl_startup_hook = original_startup_hook;
  if (original_right != NULL)
    rl_bind_keyseq("\033[C", original_right);
  for (size_t index = 0;
       index < sizeof(end_key_sequences) / sizeof(end_key_sequences[0]);
       ++index) {
    int binding_type = 0;
    rl_command_func_t *current =
        rl_function_of_keyseq(end_key_sequences[index], rl_get_keymap(),
                              &binding_type);
    if (current == accept_ghost_end)
      rl_bind_keyseq(end_key_sequences[index], rl_end_of_line);
  }
  graceful_stop_daemon();
  clear_suggestion();
  free(cached_line);
  cached_line = NULL;
  free(last_submitted_line);
  last_submitted_line = NULL;
  free(configured_histfile);
  configured_histfile = NULL;
  last_terminal_rows = 0;
  last_terminal_columns = 0;
  readline_initialized = 0;
  installed = 0;
}

char *anduinos_ghost_doc[] = {
  "Internal frontend for quiet Bash ghost-text suggestions.",
  "Use 'anduinos_ghost diagnose LINE' to inspect the current frontend state.",
  (char *)NULL
};

struct builtin anduinos_ghost_struct = {
  "anduinos_ghost",
  anduinos_ghost_builtin,
  BUILTIN_ENABLED,
  anduinos_ghost_doc,
  "anduinos_ghost [observe STATUS CWD | configure-history PATH | diagnose LINE]",
  0
};
