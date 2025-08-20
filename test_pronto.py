#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import requests
from requests.auth import HTTPBasicAuth

BASE = "https://pronto.ext.net.nokia.com/prontoapi/rest/api/latest"
USERNAME = "wyifan"        # 你给的账号
PASSWORD = "Wyywjh1018"    # 你给的密码

VERIFY_SSL = True          # 若公司证书问题导致报错，可改成 False（不推荐）
TIMEOUT = 25

# 如需要公司代理，取消下面两行注释并改成你的代理地址
# PROXY = "http://proxy.example.com:8080"
# PROXIES = {"http": PROXY, "https": PROXY}
PROXIES = None

def main():
    s = requests.Session()
    s.auth = HTTPBasicAuth(USERNAME, PASSWORD)
    s.headers.update({
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest"
    })

    try:
        # 1) 先拉 1 条 PR，验证连通性与认证
        url = f"{BASE}/problemReport?startAt=0&maxResults=1"
        print(f"[GET] {url}")
        r = s.get(url, timeout=TIMEOUT, verify=VERIFY_SSL, proxies=PROXIES)
        print(f"Status: {r.status_code}, Content-Type: {r.headers.get('Content-Type')}\n")

        if r.status_code != 200:
            print("Body preview:\n", r.text[:500])
            sys.exit(1)

        data = r.json()
        values = data.get("values", [])
        if not values:
            print("✅ 通了，但没有返回 values（可能权限或筛选原因）。原始返回：")
            print(json.dumps(data, indent=2)[:1000])
            return

        pr = values[0]
        print("✅ problemReport OK")
        print("PR ID         :", pr.get("id"))
        print("Title         :", (pr.get("title") or "").strip())
        print("State         :", pr.get("state"))
        print("SoftwareRel   :", pr.get("softwareRelease"))
        print("SoftwareBuild :", pr.get("softwareBuild"))
        print("FaultAnalysis :", pr.get("faultAnalysisId"))
        print()

        # 2) 如有 FA，拉一下 FA 详情再验证一次
        fa_id = pr.get("faultAnalysisId")
        if fa_id:
            fa_url = f"{BASE}/faultAnalysis/{fa_id}"
            print(f"[GET] {fa_url}")
            fa_r = s.get(fa_url, timeout=TIMEOUT, verify=VERIFY_SSL, proxies=PROXIES)
            print(f"Status: {fa_r.status_code}, Content-Type: {fa_r.headers.get('Content-Type')}\n")
            if fa_r.status_code == 200 and "application/json" in (fa_r.headers.get("Content-Type") or "").lower():
                fa = fa_r.json()
                print("✅ faultAnalysis OK")
                print("Identification :", (fa.get("identification") or "")[:200].replace("\n"," "))
                print("Resolution     :", (fa.get("resolution") or "")[:200].replace("\n"," "))
                print("SubSystem      :", fa.get("subSystem"))
                print("RootCause      :", fa.get("rootCause"))
            else:
                print("FA body preview:\n", fa_r.text[:500])
        else:
            print("⚠️ 该 PR 没有 faultAnalysisId，跳过 FA 测试。")

    except requests.exceptions.SSLError as e:
        print("❌ SSL 错误：可能需要公司根证书或将 VERIFY_SSL=False 试一下")
        print(e)
        sys.exit(2)
    except requests.exceptions.ProxyError as e:
        print("❌ 代理错误：如需代理请设置 PROXIES 变量")
        print(e)
        sys.exit(3)
    except requests.exceptions.ConnectTimeout:
        print("❌ 连接超时：检查公司网络/VPN/代理或主机名是否可达")
        sys.exit(4)
    except requests.exceptions.HTTPError as e:
        print("❌ HTTP 错误：", e)
        sys.exit(5)
    except Exception as e:
        print("❌ 其它错误：", e)
        sys.exit(6)

if __name__ == "__main__":
    main()
