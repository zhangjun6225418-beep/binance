# 火种计划：双选币器云端扫描（邮件提醒）

每 15 分钟由 GitHub Actions 定时执行；仅读取币安 USDⓈ-M USDT 永续公开行情，不使用交易 API Key，不自动下单。

## 两套规则

共同条件：
- USDT 永续合约
- 24h quote volume >= 10,000,000 USDT
- 候选按成交额降序，最多 200 个
- 15m 最近一根已收盘 K 线 EMA7 > EMA14

A 多周期趋势筛选：
- 日线 EMA7 > EMA14
- 周线 EMA7 > EMA14
- 最近两根已收盘 4h K 线均 EMA7 > EMA14
- 最新已收盘 4h close < EMA7

B 4H 偏离筛选：
- 最新已收盘 4h EMA7 <= EMA60 * 0.90（即 EMA7 至少低于 EMA60 10%）

EMA 使用简单平均作为初始值，再按标准递推公式计算。所有规则只使用已收盘 K 线。

## 部署步骤

1. 在 GitHub 新建一个 **Private** repository，例如 `huozhong-cloud-screener`。
2. 上传本目录全部文件，保留 `.github/workflows/scan.yml` 路径。
3. 打开仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加以下 secrets：
   - `SMTP_HOST`：邮件服务商 SMTP 地址
   - `SMTP_PORT`：通常为 `587`
   - `SMTP_USER`：SMTP 登录账号
   - `SMTP_PASSWORD`：邮件服务商生成的 SMTP 授权码/应用密码，不是 ChatGPT 密码
   - `MAIL_FROM`：发件地址（可与 SMTP_USER 相同）
   - `MAIL_TO`：接收提醒的邮箱
4. 在 **Settings → Actions → General → Workflow permissions** 中允许 workflow 写入 repository（Read and write permissions），否则无法保存去重状态。
5. 打开 **Actions → Huozhong USDT Perpetual Screeners → Run workflow**，手动运行一次测试。
6. 确认测试邮件收到后，定时任务会按 GitHub Actions 的调度执行。GitHub 的 schedule 可能延迟，不保证每次精确在整刻执行。

## 去重策略
只在某个币种从“不符合”变成“符合”时发一封新邮件；若持续符合，不重复轰炸。若之后退出条件、再次重新符合，会再次提醒。状态保存在 `state.json` 并由 workflow 提交。

## 安全与限制
- 只使用公开行情端点，不配置 Binance API Key。
- SMTP 密码只放在 GitHub Actions Secrets，切勿提交到代码或发到聊天里。
- 公共行情接口或网络偶发失败时，日志会记录被跳过的标的；请检查 Actions 运行日志。
- 每次扫描最多检查成交额达标排名前 200 的合约；EMA 实现以当前脚本公式为准，建议先人工核对信号。
- 邮件是外部提醒渠道，不会向 ChatGPT 对话直接推送消息。
