/*
 * MovieDB.app 的启动器（Contents/MacOS/MovieDB）。
 *
 * 用内置的 Python（Contents/MacOS/MovieDB-python）运行 Contents/Resources/app/desktop.py，
 * 并直接替换为 Python 进程：
 *   - Python 位于 Contents/MacOS 里，进程属于 MovieDB.app，程序坞显示 MovieDB 的名称与图标，
 *     菜单栏、通知与“再次打开”（点程序坞图标、在访达中双击）都交给同一个进程处理。
 *   - PYTHONHOME 指向 Contents/Resources/runtime/python（标准库与依赖），并清除本机其他
 *     PYTHON* 环境变量；-B 不写字节码（.app 里的文件改动会破坏签名），-s 不读用户 site-packages。
 */
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

extern char **environ;

static int parent_dir(char *path) {
    char *slash = strrchr(path, '/');
    if (slash == NULL || slash == path) return 0;
    *slash = '\0';
    return 1;
}

static void clear_python_environment(void) {
    for (int again = 1; again;) {
        again = 0;
        for (char **entry = environ; *entry != NULL; entry++) {
            if (strncmp(*entry, "PYTHON", 6) != 0) continue;
            char name[256];
            size_t length = strcspn(*entry, "=");
            if (length >= sizeof name) continue;
            memcpy(name, *entry, length);
            name[length] = '\0';
            unsetenv(name);
            again = 1; /* unsetenv 会改动 environ，重新遍历 */
            break;
        }
    }
}

int main(int argc, char *argv[]) {
    char executable[PATH_MAX], contents[PATH_MAX], python[PATH_MAX], home[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof executable;
    if (_NSGetExecutablePath(executable, &size) != 0 || realpath(executable, contents) == NULL) {
        fprintf(stderr, "MovieDB: 无法确定程序位置\n");
        return 1;
    }
    /* .../MovieDB.app/Contents/MacOS/MovieDB → .../MovieDB.app/Contents */
    if (!parent_dir(contents) || !parent_dir(contents)) return 1;
    snprintf(python, sizeof python, "%s/MacOS/MovieDB-python", contents);
    snprintf(home, sizeof home, "%s/Resources/runtime/python", contents);
    snprintf(script, sizeof script, "%s/Resources/app/desktop.py", contents);

    clear_python_environment();
    setenv("PYTHONHOME", home, 1);

    char **args = calloc((size_t)argc + 4, sizeof *args);
    if (args == NULL) return 1;
    int count = 0;
    args[count++] = python;
    args[count++] = "-B";
    args[count++] = "-s";
    args[count++] = script;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) != 0) args[count++] = argv[i]; /* 旧系统由访达附加的进程序号 */
    }
    args[count] = NULL;

    execv(python, args);
    perror("MovieDB: 无法启动内置 Python");
    return 127;
}
