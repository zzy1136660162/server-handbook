# Server Handbook · 服务器工作手册

让人和 AI 从固定入口接手服务器，按约定维护项目，并为通过工具执行的变更自动留下实际记录。

[English](README.md) · [架构与数据归属](docs/architecture.md) · [完整命令](docs/cli.md) · [MCP 接入](docs/mcp.md)

[GitHub 源码](https://github.com/zzy1136660162/server-handbook) · [提交 Issue](https://github.com/zzy1136660162/server-handbook/issues) · [发布到 PyPI](docs/pypi-publishing.zh-CN.md)

当前为 **0.1.0 Alpha**，已提供源码和本地构建包，尚未发布到 PyPI。适合个人开发者、小团队、自托管服务器维护者。现有项目可以保留原来的位置；工具保留 README、AGENTS 中人工编写的内容。

## 安装

需要 Python 3.11 以上。进入本项目目录，使用独立环境安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[mcp]'
server-handbook --help
```

Windows 可使用 `.venv\Scripts\Activate.ps1`，也可直接调用环境内的 Python。已安装 pipx 时，可以从源码目录执行 `pipx install '.[mcp]'`。基础 CLI 没有第三方依赖，MCP 是可选附加功能。

源码可先从 GitHub 获取：

```bash
git clone https://github.com/zzy1136660162/server-handbook.git
cd server-handbook
```

仓库公开，欢迎提 Issue 或通过 Fork 提交 PR。普通访客不能直接推送或合并到原仓库；是否采纳由维护者决定。

## 第一次使用

```bash
# 默认只预览；加 --apply 才创建目录和工具文件。
server-handbook init --workspace /srv/workspace
server-handbook init --workspace /srv/workspace --apply
cd /srv/workspace
server-handbook handoff
server-handbook project new demo --name '演示项目' --kind compose --apply
```

新项目生成 `README.md`、`AGENTS.md`、`src/deploy/config/data/logs/docs` 的说明，以及 `deploy/registry/demo.json`。数据目录顶层可以放说明，数据库真实存储应使用 `data/store` 等程序专用目录；工具不会向现有程序存储目录塞 README。

生成的声明默认 `prepared`，不表示已部署。填写实际 Compose 文件、project、service、端口、数据位置和需要执行的 HTTP 检查后，再登记和部署：

```bash
server-handbook register /srv/workspace/projects/demo/deploy/registry/demo.json \
  --note '首次登记已核对的部署声明；尚未执行启动'
server-handbook plan demo --action up
server-handbook apply demo --action up --expect-revision 1 \
  --note '启动已核对的服务并执行声明中的检查'
server-handbook operations --service demo
server-handbook doctor
```

已有记录先用 `show demo` 查当前 revision，不能照抄示例中的 `1`。登记失败不会覆盖旧快照；部署失败会保留失败凭条，CLI 返回非零退出码。执行检查、保存操作记录和刷新文档均由工具完成，不依赖 AI 最后记得手写总结。

## 接入原有项目

初始化时用重复的 `--project-root`、`--service-root` 声明实际根目录，用 `--records-dir` 指定台账位置。配置保存在工作区 `.server-handbook/config.json`。已有配置不同则拒绝覆盖，不会因重跑 init 改写目录规划。

```bash
server-handbook --config /srv/workspace/.server-handbook/config.json adopt \
  --home /opt/apps/example --id example --name '已有项目' --kind compose \
  --compose-file /opt/apps/example/compose.yaml \
  --compose-project example --compose-service web
```

先检查预览，再加 `--apply`。`adopt` 不搬动源代码或数据，不自动启动服务，也不自动把候选变成已验证部署。`discover` 只读发现常见 Compose 文件和容器候选，用途、归属、备份方式等仍需核实。

`project new --profile generic|python|node|java` 提供不同开发指导，不生成整套业务框架；已有项目自己的规范和用户当前指令优先。

## 信息登记在哪里

| 信息 | 位置 |
| --- | --- |
| 固定接手入口 | `<workspace>/AGENTS.md`、`README.md` |
| 主机目录配置 | `<workspace>/.server-handbook/config.json` |
| 服务声明源文件 | `<service-home>/deploy/registry/<id>.json` |
| 已登记快照、历史、实际操作凭条 | `<records-dir>/state.sqlite3` |
| 服务的人读维护说明 | 服务声明中的 `readme` 路径 |

声明、现场观察和历史凭条分开。`state=deployed` 表示登记声明，当前是否运行用现场查询判断；“进程已运行”与“HTTP 检查通过”也分别记录。没有配置业务检查时，会明确标为进程检查，不能据此宣称完整业务验收通过。

## AI 接入和中断恢复

MCP 与 CLI 使用同一套核心：

```bash
server-handbook-mcp --config /srv/workspace/.server-handbook/config.json
```

默认只读。客户端需要单独配置启动命令；安装服务器端程序不会让所有 AI 客户端自动接入。按需添加 `--allow-write` 后，仅开放登记和受管理操作，不提供任意 shell。可分发的 Skill 位于 `src/server_handbook/assets/skills/server-handbook/`，安装后可用 `server-handbook skill export 目标目录 --apply` 导出。SSH 登录提示示例见 [MCP 文档](docs/mcp.md)，本包安装不会自动修改 SSH。

```bash
server-handbook operation 操作ID
server-handbook recover 操作ID --note '已检查中断位置，下一步先核对现场'
```

操作开始前会落盘；中断后保留进行中记录。`recover` 拒绝仍在运行的操作，将已无执行者的操作标为中断，不自动重放，也不回滚外部副作用。通过普通 SSH 绕过工具修改时，只能尽量发现漂移，不能保证还原修改者和原因。

Compose 只操作明确登记的 service；依赖服务需要预先启动。不会删除卷、重装系统、重新初始化数据库或启动全局 root 守护进程。

验证范围见 [验证记录](docs/verification.md)。开源贡献见 [CONTRIBUTING.md](CONTRIBUTING.md)，许可证为 [MIT](LICENSE)。
