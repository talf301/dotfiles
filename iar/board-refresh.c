// BoardRefresh.app's executable: runs `python3 <args>` (default: board.py) as a child and waits. launchd starts it
// every minute with `reconcile.py --board` (see local.session-board.plist). macOS lets an app bundle, not a bare launchd job, write board.md into iCloud Drive;
// the child inherits this app as its responsible process.
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>

extern char **environ;

int main(int argc, char **args) {
  char path[1024];
  snprintf(path, sizeof path, "%s/dotfiles/iar/board.py", getenv("HOME"));
  char *fallback[] = {"/usr/bin/python3", path, NULL};
  char **argv = fallback;
  if (argc > 1) {
    args[0] = "/usr/bin/python3";  // python3 <script> <flags…>
    argv = args;
  }
  pid_t pid;
  if (posix_spawn(&pid, argv[0], NULL, NULL, argv, environ) != 0) return 1;
  int status;
  waitpid(pid, &status, 0);
  return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
