// BoardRefresh.app's executable: runs board.py as a child and waits. launchd starts it every minute (see
// local.session-board.plist). macOS lets an app bundle, not a bare launchd job, write board.md into iCloud Drive;
// the child inherits this app as its responsible process.
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>

extern char **environ;

int main(void) {
  char path[1024];
  snprintf(path, sizeof path, "%s/dotfiles/iar/board.py", getenv("HOME"));
  char *argv[] = {"/usr/bin/python3", path, NULL};
  pid_t pid;
  if (posix_spawn(&pid, argv[0], NULL, NULL, argv, environ) != 0) return 1;
  int status;
  waitpid(pid, &status, 0);
  return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
