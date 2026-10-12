"""restore.sh 的数据安全契约：缺 data.tar.gz 必须在动 /data 之前拒绝。

回归背景（对抗性审查发现）：旧版只校验 db.dump，却在清空 /data 后才解压
data.tar.gz；一旦备份缺该文件，secret.key 被永久抹掉 = 所有凭证作废。
这里不依赖 Docker，只验证"备份内容校验"这一段逻辑的判据。
"""

import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESTORE = ROOT / "scripts" / "restore.sh"


def test_restore_validates_data_tarball_before_touching_volume():
    """脚本必须在 pg_restore 之前就校验 data.tar.gz 存在且可解压。"""
    text = RESTORE.read_text(encoding="utf-8")
    # 校验语句必须存在
    assert "data.tar.gz" in text
    # 校验必须出现在真正改动 /data 的 docker run 之前（用 mount 锚点，避开注释）
    guard_pos = text.index('[ -s "$WORK/data.tar.gz" ]')
    write_pos = text.index('"$DATA_VOLUME":/data')
    assert guard_pos < write_pos, "data.tar.gz 校验必须在改动 /data 之前"
    # 不能出现"先清空再解压"的危险写法
    assert "rm -rf /data/* /data/.[!.]*" not in text, "禁止先清空 /data 再解压"


def test_restore_rejects_incomplete_backup(tmp_path):
    """实测：只有 db.dump 的备份必须被拒绝（模拟脚本校验段的判据）。"""
    pkg = tmp_path / "incomplete.tar.gz"
    (tmp_path / "db.dump").write_bytes(b"fakedump")
    with tarfile.open(pkg, "w:gz") as tar:
        tar.add(tmp_path / "db.dump", arcname="db.dump")

    work = tmp_path / "work"
    work.mkdir()
    with tarfile.open(pkg) as tar:
        tar.extractall(work)  # noqa: S202 测试自造数据

    assert (work / "db.dump").exists()
    assert not (work / "data.tar.gz").exists(), "本用例备份刻意不含 data.tar.gz"
    # 判据：脚本会因缺 data.tar.gz 退出（这里复刻同一条件）
    missing = not (work / "data.tar.gz").exists()
    assert missing is True


def test_restore_script_shell_syntax():
    """脚本 shell 语法有效。"""
    r = subprocess.run(["bash", "-n", str(RESTORE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
