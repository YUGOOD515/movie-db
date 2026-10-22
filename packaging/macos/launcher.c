/*
 * MovieDB.app 的启动器（Contents/MacOS/MovieDB）。
 *
 * 用内置的 Python 运行 Contents/Resources/app/desktop.py：
 *   -B 不写字节码（.app 里的文件改动会破坏签名），-s 不读用户 site-packages，
 *   -E 忽略本机的 PYTHONHOME / PYTHONPATH 等环境变量。
 *
 * 从访达或程序坞打开时，启动器在后台运行 MovieDB 后立即退出：MovieDB 只在菜单栏显示图标，
 * 再次打开 MovieDB.app 时新进程会发现已在运行，直接在浏览器中打开页面。
 * 在终端里运行时则直接替换为 Python 进程，便于查看输出。
 */
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int parent_dir(char *path) {
    char *slash = strrchr(path, '/');
    if (slash == NULL || slash == path) return 0;
    *slash = '\0';
    return 1;
}

int main(int argc, char *argv[]) {
    char executable[PATH_MAX], contents[PATH_MAX], python[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof executable;
    if (_NSGetExecutablePath(executable, &size) != 0 || realpath(executable, contents) == NULL) {
        fprintf(stderr, "MovieDB: 无法确定程序位置\n");
        return 1;
    }
    /* .../MovieDB.app/Contents/MacOS/MovieDB → .../MovieDB.app/Contents */
    if (!parent_dir(contents) || !parent_dir(contents)) return 1;
    snprintf(python, sizeof python, "%s/Resources/runtime/python/bin/python3", contents);
    snprintf(script, sizeof script, "%s/Resources/app/desktop.py", contents);

    char **args = calloc((size_t)argc + 5, sizeof *args);
    if (args == NULL) return 1;
    int count = 0;
    args[count++] = python;
    args[count++] = "-B";
    args[count++] = "-s";
    args[count++] = "-E";
    args[count++] = script;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) != 0) args[count++] = argv[i]; /* 旧系统由访达附加的进程序号 */
    }
    args[count] = NULL;

    if (!isatty(STDOUT_FILENO)) {
        pid_t child = fork();
        if (child < 0) {
            perror("MovieDB: fork");
            return 1;
        }
        if (child > 0) return 0;
        setsid();
    }
    execv(python, args);
    perror("MovieDB: 无法启动内置 Python");
    _exit(127);
}
