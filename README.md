# TV Suite

电视直播客户端与直播源管理服务的统一开发仓库。服务端负责采集、检测并发布 M3U 列表，Android TV 客户端读取列表并直接播放视频。

| 目录 | 内容 |
| --- | --- |
| [android-tv/](android-tv/) | 基于 lizongying/my-tv 的订阅客户端，可与原版并存 |
| [source-manager/](source-manager/) | 基于 Guovin/iptv-api 的采集和检测服务，使用 Docker 部署 |

两个项目独立构建和运行，通过 M3U 订阅接口连接。源管理服务已增加家庭局域网网页后台，可管理订阅、控制任务、查看检测结果、屏蔽线路和调整设置；无需登录。详见 [服务端文档](source-manager/docs/manager.md)。

## 私人配置

所有 `private/` 目录均被 Git 忽略，不上传家庭地址、订阅配置、部署记录和个人 APK。

- 客户端：将 `android-tv/playlist.properties.example` 复制到 `android-tv/private/local.properties`，填写订阅地址。
- 服务端：私人部署资料保存在 `source-manager/private/`，通过卷挂载提供给容器；该目录也已从其 Docker 构建上下文排除。

## 构建与运行

客户端需要 JDK 17 和 Android SDK 34，在 `android-tv/` 中执行：

```sh
./gradlew testDebugUnitTest assembleDebug
```

服务端的 Docker 构建文件和部署说明位于 `source-manager/`。在该目录构建本仓库源码镜像：

```sh
docker build -f Dockerfile.manager -t tv-suite/source-manager:local .
```

服务端上游文档中出现的远程镜像地址属于上游项目，不代表本仓库已发布自己的镜像。

## 来源与许可证

保留客户端 Git 历史；服务端以当前源码快照导入，来源版本为 Guovin/iptv-api `6ea026f`，并包含我们的本地目录隔离改动。保留两者版权说明，不以一个新许可证覆盖全部代码。

- 客户端来源：[lizongying/my-tv](https://github.com/lizongying/my-tv)，版权情况见其目录内 README 和 UPSTREAM_README。
- 服务端来源：[Guovin/iptv-api](https://github.com/Guovin/iptv-api)，保留目录内 AGPL-3.0 LICENSE。
