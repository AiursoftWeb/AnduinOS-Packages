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

static rl_voidfunc_t *original_redisplay;
static rl_command_func_t *original_right;
static rl_hook_func_t *original_startup_hook;
static int daemon_fd = -1;
static pid_t daemon_pid = -1;
static char *suggestion;
static char *cached_line;
static char *last_submitted_line;
static int ghost_visible;
static int installed;

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
  shell_histfile = get_string_value("HISTFILE");
  history_setting = get_string_value("ANDUINOS_GUESS_HISTORY");
  persist_setting = get_string_value("ANDUINOS_GUESS_PERSIST");
  if (access(binary, X_OK) != 0 ||
      socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) != 0)
    return -1;

  child = fork();
  if (child < 0) {
    close(sockets[0]);
    close(sockets[1]);
    return -1;
  }
  if (child == 0) {
    int nullfd;
    close(sockets[0]);
    if (shell_path != NULL)
      setenv("PATH", shell_path, 1);
    if (shell_histfile != NULL && *shell_histfile != '\0')
      setenv("ANDUINOS_BASH_HISTFILE", shell_histfile, 1);
    else
      unsetenv("ANDUINOS_BASH_HISTFILE");
    if (history_setting != NULL)
      setenv("ANDUINOS_GUESS_HISTORY", history_setting, 1);
    if (persist_setting != NULL)
      setenv("ANDUINOS_GUESS_PERSIST", persist_setting, 1);
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
    execl(binary, binary, (char *)NULL);
    _exit(127);
  }

  close(sockets[1]);
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

static int display_width(const char *text)
{
  mbstate_t state = {0};
  const char *cursor = text;
  size_t remaining = strlen(text), consumed;
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

static void ghost_redisplay(void)
{
  int width;
  char movement[32];
  int movement_length;

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

static void install_readline_hooks(void)
{
  int binding_type = 0;
  rl_command_func_t *current;
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

int anduinos_ghost_builtin(WORD_LIST *list)
{
  if (!predictions_enabled()) {
    suspend_predictions();
    return EXECUTION_SUCCESS;
  }
  install_readline_hooks();
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
  original_redisplay = rl_redisplay_function;
  original_startup_hook = rl_startup_hook;
  rl_startup_hook = ghost_startup;
  install_readline_hooks();
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
  graceful_stop_daemon();
  clear_suggestion();
  free(cached_line);
  cached_line = NULL;
  free(last_submitted_line);
  last_submitted_line = NULL;
  installed = 0;
}

char *anduinos_ghost_doc[] = {
  "Internal frontend for quiet Bash ghost-text suggestions.",
  (char *)NULL
};

struct builtin anduinos_ghost_struct = {
  "anduinos_ghost",
  anduinos_ghost_builtin,
  BUILTIN_ENABLED,
  anduinos_ghost_doc,
  "anduinos_ghost [observe STATUS CWD]",
  0
};
