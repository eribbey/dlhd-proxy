# dlhd-proxy 🚀

This is a fork of a self-hosted IPTV proxy built with [Reflex](https://reflex.dev), enabling you to watch over 1,000 📺 TV channels and search for live events or sports matches ⚽🏀. Stream directly in your browser 🌐 or through any media player client 🎶. You can also download the entire playlist (`playlist.m3u8`) and integrate it with platforms like Jellyfin 🍇 or other IPTV media players.

**10/19/25 - Updated in accordance with DLHD API changes from upstream repo. Should be working again.** 

**If using Jellyfin, use version 10.10.7 for now. Working on a fix for version 10.11+**

 ---

## ✨ Features

- **📱 Stream Anywhere**: Watch TV channels on any device via the web or media players.
- **🔎 Event Search**: Quickly find the right channel for live events or sports.
- **📄 Playlist Integration**: Download the `playlist.m3u8` and use it with Jellyfin or any IPTV client.
- **🔒 Private Playback**: Browser streaming fetches keys, init segments, and media chunks through the proxy so your device never contacts the upstream host directly.
- **🗓️ XMLTV Guide**: Access scheduling information at `guide.xml` for use with media servers like Jellyfin.
- **✅ Channel Filtering**: Select which channels appear in the playlist and generated guide.
- **🕒 Daily Guide Updates**: Automatically refresh `guide.xml` once per day at a user-defined time.
- **⚙️ Docker-First Hosting**: Run the application using Docker or Docker Compose with flexible configuration options.

---

## 🐳 Docker Installation (Required)

> ⚠️ **Important:** When exposing the application on your local network (LAN), set `API_URL` in your `.env` file to the **local IP address** of the server hosting the container.

1. Install Docker and Docker Compose.
2. Clone the repository and change into the project directory.
3. Copy the example configuration files and adjust them for your environment:
   ```bash
   cp .env.example .env
   cp docker-compose.yml.example docker-compose.yml
   ```
   The copied files are git-ignored so any local tweaks won't appear as repository changes.
4. Start the application with Docker Compose:
   ```bash
   docker compose up -d
   ```

   The compose file mounts a local `data/` directory into the container so that
   channel selections saved through the UI persist across rebuilds.

To run with plain Docker:

```bash
docker build -t dlhd-proxy .
docker run -p 3000:3000 dlhd-proxy
```

---

## ⚙️ Configuration

### Environment Variables

- **PORT**: Set a custom port for the server.
- **API_URL**: Set the domain or IP where the server is reachable.
- **SOCKS5**: Proxy DLHD traffic through a SOCKS5 server if needed.
- **PROXY_CONTENT**: Proxy video content itself through your server (optional).
- **TZ**: Timezone used for schedules and guide generation (e.g., `America/New_York`).
- **GUIDE_UPDATE**: Daily time (`HH:MM`) to refresh `guide.xml`.

Copy `.env.example` to `.env` (as shown above) and edit the local `.env` to customise your environment variables.

### Channel Selection Persistence

Custom channel selections are stored in `data/selected_channels.json`. This
directory is mounted as a Docker volume, so the preferences persist when the
container is rebuilt or updated.

### Example Docker Command
```bash
docker build --build-arg PROXY_CONTENT=FALSE --build-arg API_URL=https://example.com --build-arg SOCKS5=user:password@proxy.example.com:1080 -t dlhd-proxy .
docker run -e PROXY_CONTENT=FALSE -e API_URL=https://example.com -e SOCKS5=user:password@proxy.example.com:1080 -p 3000:3000 dlhd-proxy
```

---

## 🗺️ Site Map

### Pages Overview:

- **🏠 Home**: Browse and search for TV channels.
- **📺 Live Events**: Quickly find channels broadcasting live events and sports.
- **📥 Playlist Download**: Download the `playlist.m3u8` file for integration with media players.

---

## 📚 Hosting Options

Check out the [official Reflex hosting documentation](https://reflex.dev/docs/hosting/self-hosting/) for more advanced self-hosting setups!
