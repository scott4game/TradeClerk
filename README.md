# TradeClerk

外贸销售 AI Agent：询盘、产品目录、报价、贸易条款、出货查询、业务员跟进。

源码公开，便于客户审计；不允许把本软件作为竞品 SaaS / 托管服务对外出售。

## 运行

需要 Python 3.10+。未设置 `OPENAI_API_KEY` 时走 mock 模式（不调用大模型，只演示接口和工具）。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn tradeclerk.app:app --host 0.0.0.0 --port 8080
```

或：

```bash
ADDR=:8080 tradeclerk
```

打开 http://127.0.0.1:8080 。可选环境变量：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`（默认 `gpt-4o-mini`）、`ADDR`（默认 `:8080`）。

## License

[FSL-1.1-ALv2](LICENSE.md)（Functional Source License 1.1，两年后转为 Apache-2.0）。

**可以：** 查看、修改、自用、非商用教学/研究；为已获授权的客户做实施交付。

**不可以：** 把本软件（或实质相同功能）作为商业产品或云服务提供给他人。

**两年后：** 每一个已发布版本，自发布日起满两年，自动可按 Apache-2.0 使用。

若要合法运营与本软件同类的托管服务，需另行取得商业许可。

发布到 GitHub 前，请把 `LICENSE.md` 里的版权人 `TradeClerk` 改成你的真实姓名或公司全称。
