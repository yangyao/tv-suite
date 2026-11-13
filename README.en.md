English | [简体中文](README.md)

# TV Suite

A unified development repository for a live TV client and a live-source management service. The server side collects, checks, and publishes M3U playlists; the Android TV client reads the playlist and plays channels directly.

> Note: Other documentation in this repository (such as `docs/` and `source-manager/docs/`) is written in Chinese.

| Directory | Contents |
| --- | --- |
| [android-tv/](android-tv/) | Subscription client based on lizongying/my-tv, able to coexist with the original app |
| [source-manager/](source-manager/) | Collection and checking service based on Guovin/iptv-api, deployed with Docker |

The two projects are built and run independently and are connected through the M3U subscription endpoint. The source management service adds a home LAN web backend that can manage subscriptions, control tasks, view check results, block streams, and adjust settings; no login is required. See the [server documentation](source-manager/docs/manager.md) for details.

## Private configuration

All `private/` directories are ignored by Git, so home addresses, subscription settings, deployment records, and personal APKs are never uploaded.

- Client: copy `android-tv/playlist.properties.example` to `android-tv/private/local.properties` and fill in the subscription URL.
- Server: private deployment data is kept in `source-manager/private/` and provided to the container through a volume mount; this directory is also excluded from its Docker build context.

## Build and run

The client requires JDK 17 and Android SDK 34. Run in `android-tv/`:

```sh
./gradlew testDebugUnitTest assembleDebug
```

The server's Docker build files and deployment instructions are located in `source-manager/`. To build a source image of this repository, run in that directory:

```sh
docker build -f Dockerfile.manager -t tv-suite/source-manager:local .
```

Remote image addresses that appear in the upstream server documentation belong to the upstream project and do not mean that this repository has published its own image.

## Origins and licenses

The client's Git history is preserved; the server was imported as a snapshot of the current source, based on Guovin/iptv-api `6ea026f`, and includes our local directory isolation changes. The copyright notices of both are preserved, and no single new license is applied to all the code.

- Client origin: [lizongying/my-tv](https://github.com/lizongying/my-tv); see the README and UPSTREAM_README in that directory for copyright details.
- Server origin: [Guovin/iptv-api](https://github.com/Guovin/iptv-api); the AGPL-3.0 LICENSE in that directory is preserved.
