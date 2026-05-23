#!/usr/bin/env python3
"""轮询监控 GitHub Actions run 状态。

用法:
  scripts/watch-run.py <run_id> [--repo zh2250635/sukisu-oneplus-build]
                                [--interval 15]
                                [--proxy http://127.0.0.1:10808]
                                [--max-wait 7200]

行为:
  - 每 interval 秒查一次 run + steps
  - 仅当步骤状态变化时打印（避免刷屏）
  - run 结束（success/failure/cancelled/timed_out）立即退出
  - 退出码: 0=success, 1=failure/cancelled/timeout, 2=工具/网络错误
"""

import argparse
import json
import os
import subprocess
import sys
import time


def gh_cli(args, env):
    """调用 gh CLI，返回 json 或抛异常."""
    cmd = ["/opt/homebrew/bin/gh"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"gh failed: {r.stderr.strip()[:500]}")
    return json.loads(r.stdout) if r.stdout.strip() else {}


def fmt_step(step):
    """格式化一行 step 状态."""
    status = step.get("status", "?")
    concl = step.get("conclusion", "")
    marker = {
        "success": "✅",
        "failure": "❌",
        "skipped": "⏭️ ",
        "cancelled": "🚫",
    }.get(concl, "")
    if status == "in_progress":
        marker = "🔄"
    elif status == "queued" or status == "pending":
        marker = "⏳"
    return f"  {marker} {step['name']}  [{status}/{concl or '-'}]"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run_id", help="GitHub Actions run id")
    p.add_argument("--repo", default="zh2250635/sukisu-oneplus-build")
    p.add_argument("--interval", type=int, default=15, help="轮询间隔秒数")
    p.add_argument("--proxy", default="http://127.0.0.1:10808")
    p.add_argument("--max-wait", type=int, default=7200, help="最长等待秒数，超时退出码 1")
    args = p.parse_args()

    env = os.environ.copy()
    if args.proxy:
        env["https_proxy"] = args.proxy
        env["http_proxy"] = args.proxy

    print(f"📡 监控 run {args.run_id} (repo={args.repo}, interval={args.interval}s)")

    seen_step_state = {}  # (job_name, step_name) -> (status, conclusion)
    started = time.time()

    while True:
        elapsed = int(time.time() - started)
        if elapsed > args.max_wait:
            print(f"⏰ 超过 {args.max_wait}s, 放弃监控（run 仍可能继续）")
            return 1

        try:
            d = gh_cli(
                ["run", "view", args.run_id, "--repo", args.repo,
                 "--json", "status,conclusion,jobs"],
                env,
            )
        except Exception as e:
            print(f"⚠️  query error: {e}")
            time.sleep(args.interval)
            continue

        status = d.get("status", "")
        conclusion = d.get("conclusion", "")

        # 检查每个 job/step 是否有状态变化
        for job in d.get("jobs", []):
            jname = job.get("name", "?")
            for step in job.get("steps", []):
                key = (jname, step.get("name", "?"))
                state = (step.get("status", "?"), step.get("conclusion", ""))
                prev = seen_step_state.get(key)
                if prev != state:
                    seen_step_state[key] = state
                    if state[0] != "pending":  # pending 太多，不打
                        print(f"[+{elapsed:4d}s][{jname}] {fmt_step(step)}")

        if status == "completed":
            print(f"\n🏁 run 结束: conclusion={conclusion}, 耗时 {elapsed}s")
            return 0 if conclusion == "success" else 1

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断退出")
        sys.exit(2)
