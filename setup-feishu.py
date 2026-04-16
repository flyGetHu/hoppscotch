#!/usr/bin/env python3
"""Hoppscotch 飞书 SSO 配置脚本 — 写入 InfraConfig 数据库

敏感配置从 .env.setup-feishu 读取，请勿将 .env.setup-feishu 提交到版本控制。
"""

import os
import subprocess
import sys
from pathlib import Path

# ===== 配置项 =====
DB_CONTAINER = "hoppscotch_hoppscotch-db_1"
DB_USER = "hoppscotch"
DB_NAME = "hoppscotch"
ENV_FILE = Path(__file__).parent / ".env.setup-feishu"
# ===================


def load_env(path: Path) -> dict[str, str]:
    """从 .env 文件加载变量到 os.environ"""
    if not path.exists():
        print(f"错误: 环境文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()


load_env(ENV_FILE)

FEISHU_CLIENT_ID_ENC = os.environ["FEISHU_CLIENT_ID_ENC"]
FEISHU_CLIENT_SECRET_ENC = os.environ["FEISHU_CLIENT_SECRET_ENC"]
FEISHU_CALLBACK_URL = os.environ["FEISHU_CALLBACK_URL"]
FEISHU_SCOPE = os.environ["FEISHU_SCOPE"]
ALLOWED_AUTH_PROVIDERS = os.environ["ALLOWED_AUTH_PROVIDERS"]

SQL = f"""
INSERT INTO "InfraConfig" (id, name, value, "isEncrypted", "createdOn", "updatedOn")
VALUES (gen_random_uuid(), 'FEISHU_CLIENT_ID', '{FEISHU_CLIENT_ID_ENC}', true, now(), now())
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, "isEncrypted" = true, "updatedOn" = now();

INSERT INTO "InfraConfig" (id, name, value, "isEncrypted", "createdOn", "updatedOn")
VALUES (gen_random_uuid(), 'FEISHU_CLIENT_SECRET', '{FEISHU_CLIENT_SECRET_ENC}', true, now(), now())
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, "isEncrypted" = true, "updatedOn" = now();

INSERT INTO "InfraConfig" (id, name, value, "isEncrypted", "createdOn", "updatedOn")
VALUES (gen_random_uuid(), 'FEISHU_CALLBACK_URL', '{FEISHU_CALLBACK_URL}', false, now(), now())
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, "updatedOn" = now();

INSERT INTO "InfraConfig" (id, name, value, "isEncrypted", "createdOn", "updatedOn")
VALUES (gen_random_uuid(), 'FEISHU_SCOPE', '{FEISHU_SCOPE}', false, now(), now())
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, "updatedOn" = now();

INSERT INTO "InfraConfig" (id, name, value, "isEncrypted", "createdOn", "updatedOn")
VALUES (gen_random_uuid(), 'VITE_ALLOWED_AUTH_PROVIDERS', '{ALLOWED_AUTH_PROVIDERS}', false, now(), now())
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, "updatedOn" = now();

INSERT INTO "InfraConfig" (id, name, value, "isEncrypted", "createdOn", "updatedOn")
VALUES (gen_random_uuid(), 'ONBOARDING_COMPLETED', 'true', false, now(), now())
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, "updatedOn" = now();
"""


def run_sql(sql):
    cmd = ["sudo", "docker", "exec", "-i", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    result = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("ERROR:", result.stderr, file=sys.stderr)
        sys.exit(1)


def verify():
    cmd = [
        "sudo",
        "docker",
        "exec",
        DB_CONTAINER,
        "psql",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-t",
        "-c",
        """SELECT name, CASE WHEN length(value) > 30 THEN substring(value, 1, 30) || '...' ELSE value END as value FROM "InfraConfig" WHERE name LIKE 'FEISHU%' OR name = 'VITE_ALLOWED_AUTH_PROVIDERS' ORDER BY name;""",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print("\n=== 验证结果 ===")
    print(result.stdout)


if __name__ == "__main__":
    print("写入飞书 SSO 配置...")
    run_sql(SQL)
    verify()
    print("\n完成！请重启 AIO 容器使配置生效：")
    print("  sudo docker-compose --profile default -f docker-compose.override.prod.yml restart hoppscotch-aio")
