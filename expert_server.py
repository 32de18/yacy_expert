import os
import json
import requests
import argparse
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from urllib.parse import urlencode
from requests.auth import HTTPDigestAuth

CRAWLER_JOBS_FILE = "crawler_jobs.json"

app = Flask(__name__)
CORS(app)

# 默认yacy搜索服务地址
YACYSEARCH_HOST = "http://192.168.3.141:8090"
if "YACYSEARCH_HOST" in os.environ:
    YACYSEARCH_HOST = os.environ["YACYSEARCH_HOST"]

scheduler = BackgroundScheduler()
scheduler.start()

CRAWLER_BASE = "http://192.168.3.141:8090/Crawler_p.html"

# 定时任务的job_id前缀
CRAWLER_JOB_PREFIX = "crawler_job_"


def save_jobs_to_file():
    jobs = []
    for job in scheduler.get_jobs():
        print(job)
        if job.id.startswith(CRAWLER_JOB_PREFIX):
            # crontab字符串保存在job.kwargs['crontab']
            jobs.append(
                {
                    "job_id": job.id,
                    "ftp_url": job.args[0],
                    "cron": job.kwargs.get("crontab", None),
                }
            )
    with open(CRAWLER_JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def load_jobs_from_file():
    if not os.path.exists(CRAWLER_JOBS_FILE):
        return
    with open(CRAWLER_JOBS_FILE, "r", encoding="utf-8") as f:
        try:
            jobs = json.load(f)
            for job in jobs:
                job_id = job["job_id"]
                ftp_url = job["ftp_url"]
                cron = job["cron"]
                if not cron:
                    continue
                try:
                    trigger = CronTrigger.from_crontab(cron)
                    scheduler.add_job(
                        start_crawler_job,
                        trigger,
                        args=[ftp_url],
                        id=job_id,
                        replace_existing=True,
                        crontab=cron,
                    )
                except Exception as e:
                    print(f"恢复定时任务失败: {job_id}, {e}")
        except Exception as e:
            print(f"加载定时任务文件失败: {e}")


def start_crawler_job(ftp_url):
    params = {
        "crawlingstart": "on",
        "crawlingMode": "url",
        "crawlingURL": ftp_url,
        "crawlingDepth": 100,
        "mustmatch": ".*\\.(doc|docx|ppt|pptx|txt|md|pdf|wps|ofd)$",
    }
    try:
        # 打印实际调用的完整URL
        full_url = CRAWLER_BASE + "?" + urlencode(params)
        print(f"即将调用爬虫URL: {full_url}")
        resp = requests.get(
            CRAWLER_BASE, params=params, auth=HTTPDigestAuth("admin", "yacy")
        )
        print(f"触发爬虫，ftp_url={ftp_url}，状态码={resp.status_code}")
        return resp.text
    except Exception as e:
        print(f"爬虫触发失败: {e}")
        return str(e)


@app.route("/add_crawler_job", methods=["POST"])
def add_crawler_job():
    data = request.get_json()
    ftp_url = data.get("ftp_url")
    cron = data.get("cron")  # 例如 '0 2 * * *'
    if not ftp_url or not cron:
        return jsonify({"error": "ftp_url和cron为必填项"}), 400
    job_id = CRAWLER_JOB_PREFIX + str(abs(hash(ftp_url + cron)))
    # 先移除同名任务
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    trigger = CronTrigger.from_crontab(cron)
    scheduler.add_job(
        start_crawler_job,
        trigger,
        args=[ftp_url],
        id=job_id,
        replace_existing=True,
        crontab=cron,
    )
    save_jobs_to_file()
    return jsonify({"msg": "定时任务添加成功", "job_id": job_id})


@app.route("/list_crawler_jobs", methods=["GET"])
def list_crawler_jobs():
    jobs = []
    for job in scheduler.get_jobs():
        if job.id.startswith(CRAWLER_JOB_PREFIX):
            jobs.append(
                {
                    "job_id": job.id,
                    "ftp_url": job.args[0],
                    "cron": job.kwargs.get("crontab", None),
                }
            )
    return jsonify(jobs)


@app.route("/update_crawler_job", methods=["POST"])
def update_crawler_job():
    data = request.get_json()
    job_id = data.get("job_id")
    new_cron = data.get("cron")
    if not job_id or not new_cron:
        return jsonify({"error": "job_id和cron为必填项"}), 400
    job = scheduler.get_job(job_id)
    if not job:
        return jsonify({"error": "未找到该任务"}), 404
    ftp_url = job.args[0]
    scheduler.remove_job(job_id)
    trigger = CronTrigger.from_crontab(new_cron)
    scheduler.add_job(
        start_crawler_job,
        trigger,
        args=[ftp_url],
        id=job_id,
        replace_existing=True,
        crontab=new_cron,
    )
    save_jobs_to_file()
    return jsonify({"msg": "定时任务已更新", "job_id": job_id})


@app.route("/delete_crawler_job", methods=["POST"])
def delete_crawler_job():
    data = request.get_json()
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id为必填项"}), 400
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        save_jobs_to_file()
        return jsonify({"msg": "定时任务已删除", "job_id": job_id})
    else:
        return jsonify({"error": "未找到该任务"}), 404


@app.route("/yacysearch", methods=["GET"])
def yacysearch():
    query = request.args.get("query", "")
    count = request.args.get("count", 10)
    print("收到的query：", query)
    try:
        # 用GET方式转发到yacy
        resp = requests.get(
            f"{YACYSEARCH_HOST}/yacysearch.json",
            params={"query": query, "count": count},
        )
        print("yacy返回状态码：", resp.status_code)
        print("yacy返回内容：", resp.text)
        # 提取第一个 { 到最后一个 } 之间的内容
        match = re.search(r"({.*})", resp.text, re.DOTALL)
        if match:
            json_str = match.group(1)
            return jsonify(json.loads(json_str))
        else:
            return jsonify({"error": "未找到合法JSON内容"}), 500
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simple YaCy search proxy.")
    parser.add_argument(
        "--yacy_search_host",
        default=YACYSEARCH_HOST,
        type=str,
        help="http address of search host",
    )
    parser.add_argument("--port", default=5001, type=int, help="Port for the server")
    parser.add_argument(
        "--host", default="0.0.0.0", type=str, help="Local host address"
    )
    args = parser.parse_args()
    YACYSEARCH_HOST = args.yacy_search_host
    load_jobs_from_file()
    app.run(host=args.host, port=args.port, debug=True)
