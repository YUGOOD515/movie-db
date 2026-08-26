import requests
import json
import subprocess


base_url = "http://127.0.0.1:5244"
username = "admin"
password = "***REMOVED***"


def get_token(username: str, password: str): 
    """get alist authroize token"""

    url = base_url + "/api/auth/login"

    payload = json.dumps({
    "username": username,
    "password": password
    })
    headers = {
    'Content-Type': 'application/json'
    }

    response = requests.request("POST", url, headers=headers, data=payload)
    alist_token = response.json()["data"]["token"]

    return alist_token


def get_video_link(path):
    """get the link of a file"""

    alist_token = get_token(username, password)

    payload = json.dumps({
    "path": path,
    "password": password,
    "page": 1,
    "per_page": 0,
    "refresh": False
    })
    headers = {
    'Authorization': alist_token,
    'Content-Type': 'application/json'
    }

    response = requests.request("POST", base_url + "/api/fs/get", headers=headers, data=payload)

    return response.json()["data"]["raw_url"]


def play_video(video_url):

    mpc_path = r"C:\Program Files\MPC-HC\mpc-hc64.exe"
    video_url = video_url

    subprocess.Popen([mpc_path, video_url])