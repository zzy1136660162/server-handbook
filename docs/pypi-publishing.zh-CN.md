# 发布到 PyPI

本项目采用 **GitHub Actions + PyPI Trusted Publishing（OIDC）**。仓库公开和上传 PyPI 是两件独立的事：公开代码不会自动上传安装包，本工作流只允许维护者在 `main` 分支手动触发。

**0.1.0 已于 2026-11-13 发布到 [PyPI](https://pypi.org/project/server-handbook/0.1.0/)**，构建和上传均已通过。[首次发布记录](https://github.com/zzy1136660162/server-handbook/actions/runs/43378082055)

以下一次性配置供维护者核对；本项目后续版本复用现有 Trusted Publisher。新建自己的发行项目时，PyPI 账号登录和一次性绑定需要账号所有者在官方网站完成，不需要把密码或长期 API token 写进仓库。

## 一次性配置

### 1. 准备 PyPI 账号

在 [PyPI](https://pypi.org/account/register/) 注册或登录，验证邮箱，并在账号设置中完成双因素认证。PyPI 对账号强制要求 2FA；按网页提示保存并验证恢复码，再添加认证器或安全设备。不要把恢复码发到 Issue 或聊天里。[PyPI 官方 2FA 说明](https://docs.pypi.org/your-account/two-factor-authentication/)

### 2. 核对 GitHub 发布环境

仓库：[zzy1136660162/server-handbook](https://github.com/zzy1136660162/server-handbook)。进入 **Settings → Environments → pypi**，核对：

- 部署来源仅为 `main`。
- 默认不设置 Required reviewers；由仓库所有者手动触发后，构建通过即可发布，无需再确认一次。
- 后续有多人共同维护、需要发布复核时，可以自行增加 Required reviewers。单人维护时若启用该选项，应允许所有者审批自己的部署，避免发布流程无法继续。

工作流中的 `environment: pypi` 只引用环境名称；这些保护规则必须在 GitHub 设置里存在，不能仅凭 YAML 宣称已生效。日常 push、Issue 和外部 Pull Request 都不会触发本发布工作流。

### 3. 在 PyPI 添加 Pending Publisher

第一次发布尚未创建项目时，打开 [PyPI 账号的 Publishing 页面](https://pypi.org/manage/account/publishing/)，添加 GitHub 类型的 **pending publisher**，填写：

| 网页字段 | 本项目填写值 |
| --- | --- |
| PyPI Project Name | `server-handbook` |
| GitHub Repository Owner | `zzy1136660162` |
| GitHub Repository Name | `server-handbook` |
| Workflow name / filename | `publish-pypi.yml` |
| Environment name | `pypi` |

Workflow 字段填写文件名 `publish-pypi.yml`，对应仓库 `.github/workflows/publish-pypi.yml`，不是 Actions 页面显示的 `Publish to PyPI` 标题。

Pending Publisher 在第一次成功上传时才创建 PyPI 项目并成为正式绑定；它不会提前占用项目名。如果该名称在上传前被其他账号注册，需要重新选择可用的发行名称并同步修改项目配置。[PyPI 官方首次发布说明](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)

如果该 PyPI 项目已经属于你，则从项目的 **Manage → Publishing** 添加相同绑定，不再创建 pending publisher。[已有项目的绑定说明](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)

## 发布一个版本

1. 确认 `src/server_handbook/__init__.py` 中的 `__version__` 是准备发布的未发布版本，变更已提交到 `main`，常规 `checks` 工作流已通过。`0.1.0` 已存在；后续发布先提升版本，不能再次上传 `0.1.0`。
2. 打开仓库 [Actions](https://github.com/zzy1136660162/server-handbook/actions)，选择 **Publish to PyPI → Run workflow**。
3. 分支选择 `main`；`version` 填当前代码版本，首版为 `0.1.0`。这个输入只做一致性核对，不会自动改版本。
4. 构建作业运行测试、构建 wheel/sdist，执行 `twine check`，再安装该 wheel 验证实际 MCP 标准输入输出连接。默认配置无需第二次审批；只有后续主动设置 Required reviewers 时，才需要在 **Review deployments** 中由授权维护者确认 `pypi`。
5. 独立的发布作业下载本次已构建的文件，通过 OIDC 上传；它没有构建步骤，也不依赖长期 PyPI token。
6. 作业全部通过后，检查 [PyPI 项目页面](https://pypi.org/project/server-handbook/) 的版本与文件，再从干净环境安装验证。

构建与发布作业分离、只在发布作业申请 `id-token: write`，是 PyPA 推荐的发布方式。[PyPA 官方工作流指南](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)

用户现在可使用：

```bash
pipx install "server-handbook[mcp]==0.1.0"
server-handbook --version
```

GitHub 的 Issue/PR 权限与 PyPI 上传权限分别管理；公开下载源码或安装包，不会给普通访问者你的仓库写入权或 PyPI 发布权。

## 后续升级与失败处理

- 下个版本先修改 `__version__`（例如 `0.1.1`），提交并推送，再手动运行发布。已发布的同名同版本文件不能覆盖；不要靠删除旧文件再重传来修补版本。
- OIDC 认证失败：对照上表核对 PyPI owner/repo、工作流**文件名**、`pypi` 环境，以及本次是否从 `main` 触发；不需要临时补一个仓库 token 绕过绑定。
- 作业等待审批：默认配置不会出现这一步；先检查 `pypi` 是否增加了 Required reviewers。确需复核时由授权维护者处理；单人维护者也不能批准时，检查是否误设为禁止本人审批，或移除不需要的额外审核规则。
- 名称冲突：PyPI 上没有项目页面不等于名称已为你保留，最终以首次上传结果为准；需要改名时同步核对 `pyproject.toml`、发布页面 URL 和本文配置表。
- 构建或测试失败：先修代码或打包问题；本流程不会越过失败的构建直接上传。

首次发布已完成。后续每个版本仍需根据实际工作流和 PyPI 文件核对结果；普通 push 不会触发上传，已经发布的版本不能覆盖。
