#!/usr/bin/env python3
"""
夸克搜索引擎调用脚本
用法: python quark_search.py <query> <timeRange>
timeRange 枚举值: OneDay | OneWeek | OneMonth | OneYear | NoLimit
"""

import sys
import json
import requests


def online_search(query: str, timeRange: str) -> dict:
    """
    联网搜索工具，能通过搜索引擎接口进行联网搜索
    :param query: 查询关键词
    :param timeRange: 查询的时间范围，枚举值为 OneDay（1天内）、OneWeek（1周内）、
                      OneMonth（1月内）、OneYear（1年内）、NoLimit（无限制）
    :return: 夸克搜索引擎返回的查询结果
    """
    url = "https://cloud-iqs.aliyuncs.com/search/unified"

    payload = json.dumps({
        "query": query,
        "timeRange": timeRange,
        "engineType": "Generic",
        "contents": {
            "mainText": True,
            "rerankScore": True
        }
    })

    headers = {
        "Authorization": "Bearer 4n5ZZdOazncJkJFlUjMmfpjxBU8tL7g2ODMzY2VjYg",
        "Content-Type": "application/json"
    }

    try:
        response = requests.request("POST", url, headers=headers, data=payload, timeout=15)
        response.raise_for_status()
        result_list = json.loads(response.text).get("pageItems", [])

        result = {
            "status": "success",
            "query": query,
            "timeRange": timeRange,
            "response": [{
                "title": x.get("title"),
                "snippet": x.get("snippet"),
                "publishedTime": x.get("publishedTime"),
                "mainText": x.get("mainText")
            } for x in result_list[:3]]
        }
    except Exception as e:
        result = {
            "status": "error",
            "query": query,
            "timeRange": timeRange,
            "error": str(e),
            "response": []
        }

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python quark_search.py <query> <timeRange>")
        print("timeRange: OneDay | OneWeek | OneMonth | OneYear | NoLimit")
        sys.exit(1)

    query = sys.argv[1]
    time_range = sys.argv[2]

    result = online_search(query, time_range)
    print(json.dumps(result, ensure_ascii=False, indent=2))
