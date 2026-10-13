# Data and assets / 数据与资源

The public clone and all nine templates need no separate Release download. Committed registry snapshots supply example contracts; OSM roads and building footprints supply a real lightweight Huangpu city, rendered with procedural geometry and vehicle meshes. A failed city load or a coordinate plane is not evidence of a successfully loaded city.

公开仓库和九个模板无需另行下载 Release 资源。契约快照提供示例类型；OSM 道路与建筑轮廓构成真实的黄浦轻量城市，采用程序生成的几何与车辆外观。载入失败或坐标平面不代表城市载入成功。

OSM-derived data is credited to **© OpenStreetMap contributors, ODbL 1.0**. Read the [lite-city attribution](scenarios/demos/traffic-accident/inputs/lite-city/ATTRIBUTION.txt) and the [authoring-map attribution](src/aeroagentsim/authoring/assets/ATTRIBUTION.txt) for sources and dataset notes.

OSM 衍生数据署名为 **© OpenStreetMap contributors，ODbL 1.0**。请保留上述轻量城市与创作地图的署名文件。

## Optional Release pack

可选 Release 精细包。It adds Huangpu building meshes, textures and display models; installation does not create objects, traffic, collision physics or flight paths. 它添加黄浦精细建筑网格、纹理与展示模型；安装不会创建对象、交通流、碰撞物理或飞行航线。

| Item / 项目 | Value / 值 |
| --- | --- |
| Pack id / 标识 | `huangpu-city` |
| Manifest version / 清单版本 | `1.1.0` (pack 1.1 / 资源包 1.1) |
| Release tag / 发布标签 | [`assets-huangpu-city-v1.1`](https://github.com/ZhiweiWei-NAMI/AeroAgentSim/releases/tag/assets-huangpu-city-v1.1) |
| Attachment / 附件 | `huangpu-city-v1.1.zip` |
| Published archive size / 已发布归档大小 | 129,165,236 bytes (about / 约 123.2 MiB) |
| SHA-256 / 校验摘要 | `044ef338f893b29839c8aba326826cf295a293d685d49be61de939caea0850ff` |

After platform installation / 平台安装后：

```sh
aeroagentsim assets fetch
aeroagentsim assets list
```

Fetch verifies the official archive's pinned SHA-256 before installation. Installation compares `pack.json` `files[].bytes` with actual file sizes and checks required files, paths and model references. An integrity error means installation failed; retain the expected/actual values and obtain the correct archive. Size checks are not per-file cryptographic hashes and do not establish licensing.

下载在安装前校验官方归档的固定 SHA-256。安装将 `pack.json` 的 `files[].bytes` 与真实文件大小比较，并检查必需文件、路径和模型引用。完整性错误表示安装失败；保留预期与实际值，重新取得正确归档。大小检查不等于逐文件密码学摘要，也不能证明许可。

Packs live outside Git at `$XDG_DATA_HOME/aeroagentsim/assets`, default `~/.local/share/aeroagentsim/assets`; the installed directory is `<asset-root>/huangpu-city/1.1.0/`. `AEROAGENTSIM_ASSETS_DIR` overrides the root; `assets list` reports the actual directory. Downloads use `<asset-root>/.downloads/` and can resume when the server supports HTTP Range.

资源保存在 Git 之外的 `$XDG_DATA_HOME/aeroagentsim/assets`，默认 `~/.local/share/aeroagentsim/assets`；安装目录为 `<asset-root>/huangpu-city/1.1.0/`。`AEROAGENTSIM_ASSETS_DIR` 可更改根目录，`assets list` 显示实际目录。下载缓存位于 `<asset-root>/.downloads/`，服务器支持 HTTP Range 时可续传。

`assets fetch --url URL` or `AEROAGENTSIM_ASSET_PACK_URL` selects a mirror. For a different trusted archive, set `AEROAGENTSIM_ASSET_PACK_SHA256` to its independently obtained SHA-256; changing the URL is not permission to skip verification. A mirror of the same official bytes uses the official digest. Do not accept an untrusted download by setting the override to its digest.

`assets fetch --url URL` 或 `AEROAGENTSIM_ASSET_PACK_URL` 可选择镜像。使用另一份可信归档时，将 `AEROAGENTSIM_ASSET_PACK_SHA256` 设为通过独立来源取得的摘要；更换 URL 不代表可跳过校验。同一官方归档的镜像沿用官方摘要，不要把不可信下载的摘要填入覆盖变量来接受它。

Offline installation accepts a ZIP or unpacked pack directory containing `pack.json`, `NOTICE.txt` and `assets/` / 离线安装可使用 ZIP 或包含上述文件的解压目录：

```sh
aeroagentsim assets install /path/to/huangpu-city-v1.1.zip
aeroagentsim assets list
```

Before local installation of a downloaded official ZIP, independently compare its digest with the table above / 本地安装已下载的官方 ZIP 前，独立比较表格中的摘要：

```python
import hashlib
from pathlib import Path

archive = Path("huangpu-city-v1.1.zip")
digest = hashlib.sha256()
with archive.open("rb") as source:
    for block in iter(lambda: source.read(1048576), b""):
        digest.update(block)
print(digest.hexdigest())
```

Run this Python snippet beside the ZIP (or change `archive` to its actual path). Local `assets install` checks manifest sizes; an unpacked directory has no ZIP digest. Automatic discovery selects the highest installed semantic version with id `huangpu-city`. Reload the viewer after installation. `AEROAGENTSIM_TRAFFIC_ASSET_ROOT` overrides discovery and points directly to an unpacked `assets` folder containing `scene.json`; clear it to restore discovery.

在 ZIP 所在目录执行此 Python 片段（或将 `archive` 改为实际路径）。本地 `assets install` 核对清单大小；解压目录没有 ZIP 摘要。自动发现选择标识为 `huangpu-city` 的已安装最高语义版本。安装后重新载入视图。`AEROAGENTSIM_TRAFFIC_ASSET_ROOT` 优先指向含 `scene.json` 的解压 `assets` 目录；清除它可恢复自动发现。

Remove a version / 移除指定版本：

```sh
aeroagentsim assets remove huangpu-city --version 1.1.0
```

Reload after removal; bundled lite is used if no detailed version remains. Omitting `--version` removes all installed versions of that id. 移除后重新加载；若没有剩余精细包，使用自带轻量城市。省略 `--version` 会移除该标识的所有已安装版本。

## Licences and provenance / 许可与来源

The code's Apache-2.0 licence does not license every asset. OSM source data has the ODbL attribution above. Building meshes, textures and each vehicle/UAV/other model have the terms and provenance stated by the downloaded pack's **`NOTICE.txt`** and referenced source notices. Read that actual file before use or redistribution; this guide does not infer its contents or claim all models have one licence. Missing or placeholder terms remain unresolved, not implied permission. Preserve NOTICE, attribution and provenance with copies. Checksum verification establishes bytes, not rights.

代码的 Apache-2.0 许可不覆盖所有资源。OSM 源数据遵循上述 ODbL 署名要求。建筑网格、纹理及车辆／无人机等各类模型的许可与来源，以下载包实际的 **`NOTICE.txt`** 及其引用的来源声明为准。使用或再分发前阅读实际文件；本文不推测它的内容，也不声称所有模型采用同一种许可。缺失或占位许可仍是未解决问题，不代表授权。复制时保留 NOTICE、署名与来源；摘要校验仅证明字节一致，不证明使用权。

See [Install / 安装](INSTALL.md), [Views](docs/concepts/views.md), [Blank-project tutorial](docs/guides/blank-project-tutorial.md) and [空白项目教程](docs/guides/blank-project-tutorial.zh-CN.md).

Optional mesh packs use format `aeroagentsim.osm2world-mesh-pack/v1` and coordinate format `aeroagentsim.osm2world-source-coordinates/v1`. When using a previously generated pack, update its format labels to these names; mesh data and coordinates are unchanged.

The downloadable city uses `aeroagentsim.asset-pack/v1`: `pack.json`, a byte-size file list, `assets/`, and `NOTICE.txt`. It is distinct from the OSM2World mesh-pack format above. [tools/build_map.py](tools/build_map.py) instead compiles OSM XML into Studio ENU geometry, not textured OSM2World meshes or a Release ZIP.

下载包采用上述资源清单格式，与 OSM2World 网格格式不同。`tools/build_map.py` 将 OSM XML 编译为 Studio ENU 几何，不会生成带纹理的 OSM2World 网格或 Release ZIP。

## Display models (pack 1.1)

Pack version 1.1.0 adds a `models` catalog to `pack.json` (same `aeroagentsim.asset-pack/v1` format; 1.0 packs without it keep working). Each entry gives `path`, `kind` (`road_vehicle`, `bicycle`, `pedestrian`, `uav`, `furniture`), `size_m` (display bounding box `[length, width, height]` in metres), glTF `forward`/`up` axes and an optional `lod` mesh. The console uses it when installed: road vehicles are drawn as sedan, taxi or police car (explicit `vehicle_class`/`traffic.actor.role` first, otherwise a deterministic hash-of-id sedan/taxi variety that is a display choice, not simulated state), and UAVs as the X500 or camera quadcopter by `lowalt.vehicle_class` (passenger uses a larger display size), with a business accent when the newcomer legend is on. Bicycle, pedestrian and street-furniture models ship in the pack but are drawn only for entities a scenario actually simulates. Without a pack the procedural stand-ins are used.

Build: `python tools/assets/build_pack.py CITY_DIR OUT.zip --catalog tools/assets/model_catalog.json --model-source MODELS_DIR`. The builder copies recorded glTF provenance; authors must fill and review all licensing notices before distribution. Invoking the builder does not complete that review.

制作工具复制已记录的 glTF 来源；发布前作者必须填写并审阅所有许可声明。执行工具不能替代审阅。
