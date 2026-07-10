# 更新源镜像到 VPS（国内可达）

让无法访问 GitHub 的用户也能检查/下载更新：客户端从 VPS（`dash.vpanel.cc`，
Cloudflare 代理、国内可达）拉 `update-manifest.json` 与安装包；GitHub 作运行时回退。

链路：`release.yml` 的 publish job 在发 GitHub Release 后，自动把 `dmg` +
`windows-setup.exe` + 一份 **VPS-URL 版 manifest** scp 到 VPS 的 updates 目录，
host nginx 静态服务它们；客户端优先用 app-config 下发的 `update_manifest_url`。

## 一次性运维（在 VPS 上）

### 1. 建 updates 目录

```bash
mkdir -p /root/dashdesign/updates
```

### 2. host nginx 加静态 location

编辑 `/etc/nginx/sites-available/dash.vpanel.cc`，在 `server {}` 里加：

```nginx
location /updates/ {
    alias /root/dashdesign/updates/;
    autoindex off;
    # 安装包较大，允许 Cloudflare/浏览器缓存
    add_header Cache-Control "public, max-age=300";
}
```

然后 `nginx -t && systemctl reload nginx`。验证：

```bash
curl -I https://dash.vpanel.cc/updates/update-manifest.json   # 首次 404 正常（还没镜像）
```

### 3.（推荐）建受限部署用户 + 部署密钥

不要给 CI root。新建只能写 updates 目录的用户：

```bash
useradd -m -s /bin/bash deploy
mkdir -p /home/deploy/.ssh && chmod 700 /home/deploy/.ssh
# 目录归属给 deploy，让它能 scp 覆盖
chown -R deploy:deploy /root/dashdesign/updates
chmod 755 /root/dashdesign/updates
# 生成密钥（在本机跑，私钥进 GitHub secret，公钥进 VPS）
#   ssh-keygen -t ed25519 -f dashdesign_deploy -N ''
# 把 dashdesign_deploy.pub 追加到：
cat dashdesign_deploy.pub >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
```

> 若嫌麻烦也可直接用现有 root（`VPS_USER=root`、`VPS_UPDATES_DIR=/root/dashdesign/updates`），
> 但把 CI 的 SSH 私钥暴露成 root 访问，安全面更大，自行权衡。

## GitHub Secrets（仓库 → Settings → Secrets → Actions）

| Secret | 值 | 说明 |
|---|---|---|
| `VPS_SSH_KEY` | 部署私钥全文（`dashdesign_deploy`） | 设了才启用镜像；不设则该步骤跳过 |
| `VPS_HOST` | **源站真实 IP** | ⚠️ 不能填 `dash.vpanel.cc`——那是 Cloudflare 橙云，SSH 不走 CF |
| `VPS_PORT` | `45605` | SSH 端口 |
| `VPS_USER` | `deploy`（或 `root`） | |
| `VPS_UPDATES_DIR` | `/root/dashdesign/updates` | 与 nginx alias 一致 |
| `VPS_UPDATES_BASE_URL` | `https://dash.vpanel.cc/updates` | manifest 里安装包的下载前缀（走 CF） |

## 客户端切源（管理员，一次）

发一版带这些改动的客户端后：**设置 → 云端配置 → 解锁 → 「更新地址」**填
`https://dash.vpanel.cc/updates/update-manifest.json` → 保存并上传云端。
全员下次启动自动改从 VPS 检查/下载更新；VPS 不可达时回退内置 GitHub 地址。

## 校验

发一个新 tag 触发 release.yml 后：

```bash
curl -sL https://dash.vpanel.cc/updates/update-manifest.json | python3 -m json.tool
# platforms.*.url 应指向 dash.vpanel.cc/updates/...，sha256 与 GitHub 一致
curl -I https://dash.vpanel.cc/updates/DashDesign-<版本>-windows-setup.exe   # 200
```
