# Installation / 安装

Use Python 3.10+, Node.js 22 and npm. Install the bundled kernel and platform in the same virtual environment. The base installation and its builtin templates include the lightweight OSM Huangpu city and registry snapshots; no separate asset pack or AeroGraph checkout is required. Installed scenario plugins may add templates. The optional detailed pack is `huangpu-city` 1.1.0: [fetch, locations, SHA-256 and licences](ASSETS.md#optional-release-pack).

Inventory and configuration validation are not readiness: `templates` lists descriptors, and validation checks configuration/declarations, not a worker, native engine, camera, model provider or detailed city. Initialize a saved version and read the returned status before expecting a run.

需要 Python 3.10+、Node.js 22 和 npm。内核与平台安装在同一个虚拟环境。基础安装和随附的内置模板自带 OSM 黄浦轻量城市与契约快照，无需另外下载资源包或 AeroGraph 仓库；安装的场景插件可以增加模板。可选精细资源包为 `huangpu-city` 1.1.0，详见[下载、位置、SHA-256 与许可](ASSETS.md#optional-release-pack)。

清单与配置校验都不等于就绪：`templates` 只列出描述，校验只检查配置与声明，不代表工作进程、原生引擎、相机、模型提供方或精细城市可用。请先初始化已保存版本并读取返回状态，再期待运行。

## Linux / macOS

From a terminal / 在终端执行：

```bash
git clone https://github.com/ZhiweiWei-NAMI/AeroAgentSim.git
cd AeroAgentSim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ./aerokernel -e '.[server]'
cd frontend
npm ci
npm run build
cd ..
```

## Windows shells / Windows 终端

Clone the repository and enter it first. Choose one shell; activation syntax differs. In these shells, use double quotes around extras.

先克隆仓库并进入目录。选择一种终端，激活命令不同；安装 extras 时使用双引号。

**cmd (Command Prompt / 命令提示符)**

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e ./aerokernel -e ".[server]"
cd frontend
npm ci
npm run build
cd ..
```

**PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ./aerokernel -e ".[server]"
cd frontend
npm ci
npm run build
cd ..
```

If script execution is blocked, invoke `.\.venv\Scripts\python.exe -m pip` directly instead of activation. The current run service explicitly uses Python's `fork` multiprocessing context, which native Windows does not provide. Use WSL2/Linux for serving and executing runs, and create a Linux virtual environment there using the Linux commands above; a Windows virtual environment is not reusable in WSL2. The shell commands above describe installation, not proof of native Windows runtime support.

若脚本执行策略阻止激活，可直接使用 `.\.venv\Scripts\python.exe -m pip`。当前运行服务使用 Python 的 `fork` 多进程上下文，原生 Windows 不提供它。服务与仿真执行请使用 WSL2/Linux，并按上面的 Linux 命令重新创建虚拟环境；Windows 虚拟环境不能复用到 WSL2。上述终端命令说明安装方法，不代表原生 Windows 运行服务已获支持。

## Camera and optional engines / 相机与可选引擎

Only scenarios taking simulation-camera photos need Chromium. In `frontend/`, install it with `npx playwright install chromium` on macOS/Windows, or `npx playwright install --with-deps chromium` on supported Linux (system-library installation can require administrator access). `AEROAGENTSIM_CHROMIUM` selects an existing executable; a missing executable is an error, not a substitute photo. See [camera setup](docs/getting-started/install.md#camera-dependencies).

只有需要仿真相机照片的场景才需要 Chromium。在 `frontend/` 中，macOS/Windows 使用 `npx playwright install chromium`，受支持的 Linux 使用 `npx playwright install --with-deps chromium`（安装系统库可能需要管理员权限）。`AEROAGENTSIM_CHROMIUM` 可指定已有可执行文件；文件缺失会产生错误，不会生成替代照片。详见[相机设置](docs/getting-started/install.md#camera-dependencies)。

Manual configuration and the built-in conversational assistant need only `[server]`; runtime LangGraph agents additionally need `python -m pip install -e '.[server,agents]'`. Configure their real provider separately: [agents](docs/guides/agents.md). Model-backed assistant runs and provider-calling `decision`/`langgraph` runs require that provider; fixture and scripted modes such as `traffic_decisions` `mode: stub` and `tools/assistant/fake_model.py` are not live-model evidence. Native simulator services are optional: [containers](docs/guides/containers.md).

手动配置与内置对话助手只需 `[server]`；运行期 LangGraph 智能体还需安装 `python -m pip install -e '.[server,agents]'`（Windows 终端改用双引号）。真实模型提供方需单独配置，见[智能体指南](docs/guides/agents.md)。真实模型助手以及调用提供方的 `decision`/`langgraph` 运行需要该提供方；`traffic_decisions` 的 `mode: stub`、`tools/assistant/fake_model.py` 等夹具与脚本模式不能作为真实模型证据。原生仿真器服务为可选项，见[容器指南](docs/guides/containers.md)。

## Post-install acceptance

安装后验收。在已激活的虚拟环境、仓库根目录中执行以下命令；预期结果是检查标准，并非本次文档修改已执行的结果。

Run these checks from the repository root in the activated environment. Expected results below are acceptance criteria, not a claim that this documentation change ran them.

```sh
python --version
node --version
npm --version
python -c "from importlib.metadata import version; print('aeroagentsim', version('aeroagentsim'))"
aeroagentsim assets list
aeroagentsim plugins
aeroagentsim templates
python -c "from pathlib import Path; p = Path('frontend/dist/index.html'); assert p.is_file(), p; print(p)"
```

| Check / 检查 | Expected / 预期 |
| --- | --- |
| Python / Node / npm | Python ≥3.10, Node v22.x, npm prints its installed version / Python ≥3.10、Node v22.x，npm 输出已安装版本 |
| Platform version / 平台版本 | `aeroagentsim 2.1.0` for this checkout; read installed metadata, as the CLI has no `--version` / 本仓库对应 `aeroagentsim 2.1.0`；CLI 没有 `--version`，以安装元数据为准 |
| `assets list` | `[]` is valid with bundled lite data; detailed installation lists `id`, `version`, `title`, `directory` (`huangpu-city`, `1.1.0`) / 只有自带轻量资源时 `[]` 正常；精细包列出标识、版本、标题、目录 |
| `plugins` | JSON inventory of builtin and installed scenario, engine, algorithm and model contributions; inventory is not readiness / 内置与已安装场景、引擎、算法、模型贡献的 JSON 清单；清单不代表就绪 |
| `templates` | JSON list of every builtin template descriptor plus any installed scenario-plugin descriptor. Every builtin id from `aeroagentsim.scenarios.TEMPLATES` is present; each row has `format: aeroagentsim.template/v1`, nonempty `id`/`title`/`summary`, a valid `category`/`difficulty`, positive `duration_s`, `scenario`, `observe`/`try` bullets and `knobs`/`kpis`. No fixed count / 内置模板与已安装场景插件模板的 JSON 列表；`aeroagentsim.scenarios.TEMPLATES` 中的每个内置标识都存在，每行包含 `format: aeroagentsim.template/v1`、非空 `id`/`title`/`summary`、有效 `category`/`difficulty`、正的 `duration_s`、`scenario`、`observe`/`try` 条目以及 `knobs`/`kpis`；不要求固定数量 |
| Static console / 控制台静态文件 | `frontend/dist/index.html` exists after the build / 构建后存在该文件 |

Start the service on Linux/macOS/WSL2, then keep that terminal open / 在 Linux/macOS/WSL2 启动服务并保留该终端：

```sh
aeroagentsim serve --frontend frontend/dist
```

In a second activated terminal, probe the existing HTTP endpoint / 在第二个已激活环境的终端检查已有 HTTP 端点：

```sh
python -c "import json, urllib.request; from aeroagentsim.scenarios import TEMPLATES; r = urllib.request.urlopen('http://127.0.0.1:8002/v1/templates', timeout=10); rows = json.load(r); required = {'format','id','title','summary','category','difficulty','duration_s','scenario','observe','try','knobs','kpis'}; assert r.status == 200 and isinstance(rows, list) and set(TEMPLATES) <= {row['id'] for row in rows} and all(required <= set(row) and row['format'] == 'aeroagentsim.template/v1' and row['id'] and row['title'] and row['summary'] for row in rows); print(r.status, len(rows), 'templates')"
```

Expected: `200 <N> templates`, where `<N>` is the number of discoverable builtin and installed descriptors; every builtin id must be present. This is a catalogue availability check, not a health/readiness probe: the app has no dedicated `/health` endpoint. It checks service/catalog access and builtin catalogue criteria, not simulator, native-engine, camera, model-provider or detailed-city readiness. Open <http://127.0.0.1:8002> to load the console; if a check fails, retain its actual error and fix that step before running a scenario.

预期：`200 <N> templates`，其中 `<N>` 是可发现的内置与已安装描述数量；每个内置标识都必须存在。这是目录可用性检查，不是健康／就绪探针：应用没有独立 `/health` 路由。该检查只证明服务与模板目录可访问，并核对内置目录条件，不证明仿真器、原生引擎、相机、模型提供方或精细城市已就绪。打开 <http://127.0.0.1:8002> 载入控制台；检查失败时保留实际错误，修复相应步骤再运行场景。

Continue with [a template](docs/getting-started/quickstart.md), or [a blank project and its region](docs/guides/blank-project-tutorial.md) ([中文教程](docs/guides/blank-project-tutorial.zh-CN.md)). [Guide index / 指南索引](docs/guides/README.md).
