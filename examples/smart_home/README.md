# smart home mcp server

```bash
cd examples/smart_home
mcp install src/smart_home/hub.py:hub_mcp -f .env
```
where `.env` contains the following:
```
HUE_BRIDGE_IP=<your hue bridge ip>
HUE_BRIDGE_USERNAME=<your hue bridge username>
```

```bash
open -a Claude
```