# 标注成果用途授权与劳动结清

联调数据采用 [《素材与标注交接》](交换约定.md) 的字段和时间口径。`data/annotation_20261204.json` 是各参与方的合成报文快照，含未核认、互相冲突或处于变化中的来源事实。

使用 Python 3.11 标准库。执行 `python3 -m unittest discover -s tests -v` 检查交换资料、本地读取与业务规则。需要给本机接入方提供样本时，可运行以下只读报文服务。

```sh
python3 -m http.server 8040 --bind 127.0.0.1 --directory data
```

在同一环境读取 `http://127.0.0.1:8040/annotation_20261204.json` 可取得 UTF-8 JSON。JSON 的缺省字段以 `null` 表示。

## 后台规则

`annotation_exchange` 是只读资料上的规则实现，不覆盖、不清理来源报文。命令行入口：

```sh
python3 -m annotation_exchange issues                  # 结构问题
python3 -m annotation_exchange deliver C2 --at 16:20   # 重新核定领用交付
python3 -m annotation_exchange revocation P2           # 撤回影响面与确认状态
python3 -m annotation_exchange reviews --worker L4     # 复核有效性
python3 -m annotation_exchange ledger --author L1      # 按版本核定劳动
python3 -m annotation_exchange visible L2 --role review
python3 -m annotation_exchange reopen C2 W3 --grantee kitchen-team
python3 -m annotation_exchange ack N1 --grantee kitchen-team --claim C1 --at 16:00
```

要点：

- 交件要按领用用途、接收组，对其引用的每个片段都有有效许可才能交付；其他用途的许可不能补缺口。
- 只撤回一段许可时，同批无关成果照常交付；受影响交件换用同作者仍合规的版本，没有则只挂起该部分。
- 通知送达与下游确认分开记录。确认以接收组身份和领用批编号核认，一个确认编号只能绑定一个领用批。
- 运营按一次撤回可查尚未确认的领用，包括受影响却尚未送达的。原编号失去许可后不能再用它打开材料。
- 复核认具体版本；自审与原作者补做对照都不算独立复核。单独退回或改判只重核相应版本，已付款保留并记录原因。
- 单纯撤回使用许可不改变已验收劳动和已到账付款。

写操作（`ack`）只改内存，进程退出即失效；需要落盘的数据由上游另行递交。

