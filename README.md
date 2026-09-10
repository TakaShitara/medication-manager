# medication-manager

## 概要

薬箱の横に置く端末から、朝のお薬・夜のお薬の服薬状況を確認して記録するための小さなWebアプリです。

状態はサーバー側の `state.json` を正として保存します。同一LAN内の別端末からアクセスしても同じ状態が表示されます。服薬と取消の操作は、設定されている場合はDiscord Webhookへ通知します。

## 必要環境

- Docker
- Docker Compose

## 環境変数

| 変数名 | 必須 | 内容 |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | 任意 | Discord Webhook URL。未設定でも服薬状態の保存は動作しますが、画面に警告が表示されます。 |
| `TZ` | 任意 | タイムゾーン。通常は `Asia/Tokyo` のまま使用します。 |

実際のWebhook URLはGitHubへ保存しないでください。ローカルで使う場合は `.env.example` を参考に `.env` を作成します。

## Dockerでの起動方法

初回ビルド:

```sh
docker compose build
```

起動:

```sh
docker compose up -d
```

停止:

```sh
docker compose down
```

再起動:

```sh
docker compose restart
```

更新後の再ビルド:

```sh
docker compose up -d --build
```

## アクセス方法

LAN内のブラウザから次の形式で開きます。

```text
http://サーバーIP:8085
```

例:

```text
http://192.168.1.10:8085
```

## Echo Show 5 / Silkでの常時表示

Echo Show 5で使う場合は、Silkブラウザを開いて medication-manager のURLへアクセスします。

```text
http://192.168.1.201:8085
```

このアプリは、Silkが無操作でホーム画面へ戻る挙動を抑えるために、ページ内で短い無音音声を継続再生するKeep Alive機能を持っています。無音音声はアプリ自身の `/static/keepalive/silence.wav` から配信され、外部CDNや外部Keep Aliveサービスには依存しません。

ブラウザの自動再生制限によりKeep Aliveが自動開始できない場合は、画面を一度タップしてください。服薬ボタンなど通常の画面操作でも開始を試みます。

Keep AliveはSilkのホーム復帰を完全に保証するものではありません。Amazon側やSilkブラウザの仕様変更により、動作しなくなる可能性があります。

## Discord Webhook設定

`DISCORD_WEBHOOK_URL` にWebhook URLを設定すると、服薬と取消の操作時にDiscordへ通知します。

ローカルで `.env` を使う例:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxxx/xxxxx
TZ=Asia/Tokyo
```

Portainer Stackでは、GitHubリポジトリに `.env` を含めず、Portainer側のEnvironment variablesで `DISCORD_WEBHOOK_URL` を設定してください。

## state.json

コンテナ内の保存場所:

```text
/app/data/state.json
```

ホスト側の想定保存場所:

```text
/srv/medication-manager/data/state.json
```

`compose.yaml` では次のbind mountを設定しています。

```yaml
/srv/medication-manager/data:/app/data
```

コンテナを削除・再作成しても、ホスト側の `state.json` が残るため当日の状態を維持できます。

## Portainer

Portainer StackからGit Repositoryを指定してデプロイする想定です。

1. GitHubのPrivate RepositoryへこのプロジェクトをPushする
2. Ubuntu Server側に `/srv/medication-manager/data` を作成する
3. PortainerでGit Repositoryを指定してStackを作成する
4. Environment variablesに `DISCORD_WEBHOOK_URL` と `TZ=Asia/Tokyo` を設定する
5. Deployする

## 更新方法

```text
Windows / Codex
↓
Git Commit
↓
GitHub Push
↓
PortainerでPull / Re-deploy
```

## 実装内容

- `GET /api/status`
- `POST /api/take/morning`
- `POST /api/take/night`
- `POST /api/cancel/morning`
- `POST /api/cancel/night`
- 日本時間 `Asia/Tokyo` による日付判定
- `state.json` の自動作成
- 日付が変わっていた場合の自動リセット
- 一時ファイル書き込み後の `os.replace` による安全な保存
- 同一プロセス内Lockによる簡易的な二重操作対策
- 服薬済み状態への重複take、未服薬状態への重複cancelの無視
- Discord Webhook通知
- Discord通知失敗時も服薬状態は保存
- 約30秒ごとの画面自動更新
- Echo Show 5 / Silk向けのローカル無音音声Keep Alive
- 960×480程度の横画面を重視したダークテーマUI

## トラブルシューティング

### Discord通知が来ない

- Portainerまたは `.env` に `DISCORD_WEBHOOK_URL` が設定されているか確認してください。
- Webhook URLがDiscord側で削除・再生成されていないか確認してください。
- コンテナから外部インターネットへHTTPS通信できるか確認してください。
- 通知に失敗しても、服薬状態は保存されます。

### state.jsonが作成されない

- ホスト側の `/srv/medication-manager/data` が存在するか確認してください。
- Dockerコンテナが `/app/data` に書き込める権限を持っているか確認してください。
- 初回アクセス時、またはAPI実行時に `state.json` は自動作成されます。

### ポートへアクセスできない

- `compose.yaml` の `8085:8080` が他サービスと衝突していないか確認してください。
- サーバーのIPアドレスとポート番号が正しいか確認してください。
- Ubuntu Server側のファイアウォールで `8085` が許可されているか確認してください。
- コンテナが起動しているか `docker compose ps` で確認してください。

### コンテナ再作成で状態が消える

- `/srv/medication-manager/data:/app/data` のbind mountが有効か確認してください。
- `state.json` がコンテナ内だけに作成されていないか確認してください。
- Portainer Stackのvolume設定が `compose.yaml` と一致しているか確認してください。

## ローカルテスト

Python環境に依存関係を入れている場合は、次でテストできます。

```sh
python -m unittest discover -s tests
```
