#ifndef ANDUINOS_BASH_READLINE_ABI_H
#define ANDUINOS_BASH_READLINE_ABI_H

/* Stable public loadable-builtin and Readline surface used by the frontend.
   Keeping these small declarations local makes cross builds independent of
   the build host's Bash and Readline development packages. */

typedef struct word_desc {
  char *word;
  int flags;
} WORD_DESC;

typedef struct word_list {
  struct word_list *next;
  WORD_DESC *word;
} WORD_LIST;

typedef int sh_builtin_func_t(WORD_LIST *);
struct builtin {
  char *name;
  sh_builtin_func_t *function;
  int flags;
  char *const *long_doc;
  const char *short_doc;
  char *handle;
};

#define BUILTIN_ENABLED 0x01
#define EXECUTION_SUCCESS 0

typedef int rl_command_func_t(int, int);
typedef int rl_hook_func_t(void);
typedef void rl_voidfunc_t(void);
struct _keymap_entry;
typedef struct _keymap_entry *Keymap;

extern rl_voidfunc_t *rl_redisplay_function;
extern rl_hook_func_t *rl_startup_hook;
extern char *rl_line_buffer;
extern int rl_point;
extern int rl_end;
extern int rl_visible_prompt_length;

extern Keymap rl_get_keymap(void);
extern rl_command_func_t *rl_function_of_keyseq(const char *, Keymap, int *);
extern int rl_bind_keyseq(const char *, rl_command_func_t *);
extern int rl_insert_text(const char *);
extern int rl_forward_char(int, int);
extern int rl_end_of_line(int, int);
extern char *get_string_value(const char *);

#endif
