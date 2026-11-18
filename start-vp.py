"""交互式启动 Qomjudge VP，在当前终端按 Ctrl+C 停止。"""
import argparse
from contextlib import ExitStack
import errno
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
import re
import socket
import sys

import psutil
import requests
from bs4 import BeautifulSoup

from app import ROOT, create_app, serve_app
from rankland import RankLandBoard
from xcpcio import board_url


PORTS = range(8080, 8181)


def read_settings():
    while True:
        try:
            contest = int(input("QOJ 比赛编号 [1802]：").strip() or "1802")
            if contest > 0:
                break
        except ValueError:
            pass
        print("比赛编号必须是正整数。")

    while True:
        choice = input("赛制：1 传统 ICPC，2 CCPC 2026 动态揭题 [1]：").strip() or "1"
        if choice in {"1", "2"}:
            break
        print("请输入 1 或 2。")

    while True:
        try:
            if choice == "1":
                url = board_url(input("XCPCIO 榜单链接（留空使用 QOJ 原榜单）：").strip())
                return contest, "icpc", url
            url = input("RankLand 榜单链接或 https://vp.xcpc.link/?contest=比赛ID：").strip()
            RankLandBoard(url)  # Also validate count and ratio; pass the original URL to the app.
            return contest, "ccpc26", url
        except ValueError as exc:
            print(exc)


def show_contest(contest, rule, url):
    page = f"https://qoj.ac/contest/{contest}"
    print(f"比赛页面：{page}\n赛制：{'CCPC 2026' if rule == 'ccpc26' else '传统 ICPC'}\n榜单：{url or 'QOJ 原榜单'}")
    try:
        with requests.get(page, timeout=(3, 5)) as response:
            response.raise_for_status()
            title_node = BeautifulSoup(response.content, "html.parser").title
            title = title_node.get_text(" ", strip=True) if title_node else ""
    except requests.RequestException:
        title = ""
    if title and not re.search(r"login|sign in|登录|just a moment|access denied", title, re.I):
        print(f"网页标题：{title}")
    else:
        print("未能确认公开比赛名称，可在浏览器中连接 QOJ 后核对。")


def same_service(port, launch_id):
    connection = HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        if response.status != 200:
            return False
        health = json.loads(response.read(4096))
        return isinstance(health, dict) and health.get("status") == "ok" and health.get("launch_id") == launch_id
    except (OSError, HTTPException, ValueError):
        return False
    finally:
        connection.close()


def show_addresses(port):
    print(f"本机：http://127.0.0.1:{port}")
    try:
        interfaces = psutil.net_if_addrs()
    except (OSError, psutil.Error):
        return
    for name in sorted(interfaces, key=lambda name: ("vmnet8" not in name.lower(), name)):
        for address in interfaces[name]:
            if address.family == socket.AF_INET and not address.address.startswith(("127.", "169.254.")):
                print(f"{name}：http://{address.address}:{port}")


def launch(contest, rule, url):
    # Only reuse a LAN server started for this project with these exact settings.
    settings = json.dumps([os.path.normcase(str(ROOT)), contest, rule, url])
    launch_id = hashlib.sha256(settings.encode()).hexdigest()
    denied = {errno.EACCES, getattr(errno, "WSAEACCES", errno.EACCES)}
    with ExitStack() as sockets:
        # Reserve the lowest free port while looking for a previous launch on a higher port.
        listener = None
        for port in PORTS:
            candidate = sockets.enter_context(socket.socket())
            option = socket.SO_EXCLUSIVEADDRUSE if os.name == "nt" else socket.SO_REUSEADDR
            candidate.setsockopt(socket.SOL_SOCKET, option, 1)
            try:
                candidate.bind(("0.0.0.0", port))
                if listener is None:
                    candidate.listen()
                    listener = candidate
                else:
                    candidate.close()
            except OSError as exc:
                candidate.close()
                if exc.errno in denied:
                    continue
                if exc.errno != errno.EADDRINUSE:
                    raise
                if same_service(port, launch_id):
                    print("已复用同一配置的反代。要停止它，请在原终端按 Ctrl+C。")
                    show_addresses(port)
                    return
        if listener is None:
            raise OSError("8080 到 8180 均不可用，请关闭不用的服务后重试。")
        app = create_app(contest, vp_rule=rule, rankland_url=url if rule == "ccpc26" else None,
                         xcpcio_url=url if rule == "icpc" else None)
        app.config["VP_LAUNCH_ID"] = launch_id
        show_addresses(listener.getsockname()[1])
        print("请在浏览器中连接 QOJ。在此终端按 Ctrl+C 可停止反代。", flush=True)
        serve_app(app, sockets=[listener])


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    settings = read_settings()
    show_contest(*settings)
    if input("启动这场比赛？[Y/n] ").strip().lower() in {"", "y", "yes", "是"}:
        launch(*settings)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print()
    except OSError as exc:
        sys.exit(f"无法启动反代：{exc}")
