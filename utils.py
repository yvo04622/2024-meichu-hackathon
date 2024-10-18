import logging
import os
import re
import requests
import json

from PIL import Image
from io import BytesIO
import urllib
import google.generativeai as genai

campus_json = json.load("campus.json")

logger = logging.getLogger(__file__)


def is_url_valid(url):
    regex = re.compile(
        r"^(?:http|ftp)s?://"  # http:// or https://
        # domain...
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
        r"localhost|"  # localhost...
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
        r"(?::\d+)?"  # optional port
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE,
    )
    return re.match(regex, url) is not None


def create_gcal_url(
    title="看到這個..請重生",
    date="20230524T180000/20230524T220000",
    location="那邊",
    description="TBC",
):
    base_url = "https://www.google.com/calendar/render?action=TEMPLATE"
    event_url = f"{base_url}&text={urllib.parse.quote(title)}&dates={date}&location={urllib.parse.quote(location)}&details={urllib.parse.quote(description)}"
    return event_url + "&openExternalBrowser=1"


def check_image(
    url=None,
    b_image=None
):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    if url is not None:
        response = requests.get(url)
        if response.status_code == 200:
            image_data = response.content
    elif b_image is not None:
        image_data = b_image
    else:
        return "None"
    logger.info(f"URL: {url} \n Image: {b_image}")
    image = Image.open(BytesIO(image_data))

    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(
        [
            """
        請幫我把圖片中的時間、地點、活動標題 以及活動內容提取出來。
        其中時間區間的格式必須符合 Google Calendar 的格式，像是 "20240409T070000Z/20240409T080000Z"。
        由於時區為 GMT+8，所以請記得將時間換算成 GMT+0 的時間。
        如果是中華民國年，請轉換成西元年，例如 110 年要轉換成 2021 年。
        content 請只保留純文字，不要有任何 HTML 標籤，並且幫忙列點一些活動的注意事項。
        不准有 markdown 的格式。
        輸出成 JSON 格式，絕對不能有其他多餘的格式，例如：
        {
            "time": "20240409T070000Z",
            "location": "台北市",
            "title": "大直美術館極限公園",
            "content": "這是一個很棒的地方，歡迎大家來參加！"
        }
        """,
            image,
        ]
    )

    logger.info(response.text)

    return response.text



def shorten_url_by_reurl_api(short_url):
    url = "https://api.reurl.cc/shorten"

    headers = {
        "Content-Type": "application/json",
        "reurl-api-key": os.getenv("REURL_API_KEY"),
    }

    response = requests.post(
        url,
        headers=headers,
        data=json.dumps(
            {
                "url": short_url,
            }
        ),
    )
    logger.info(response.json())
    return response.json()["short_url"]

import re

def replace_location_with_abbrev(input_text):
    for campus, buildings in campus_json.items():
        for building_code, building_info in buildings.items():
            tw_abbrev = building_info.get("tw-abbrev")
            tw_name = building_info.get("tw")
        
            if tw_abbrev and tw_abbrev in input_text:
                input_text = input_text.replace(tw_abbrev, building_code)
            
            if tw_name and input_text.startswith(tw_name):
                input_text = input_text.replace(tw_name, building_code)
            
            if tw_name and re.search(f"{tw_name}.*", input_text):
                input_text = re.sub(f"{tw_name}", building_code, input_text)

            input_text = re.sub(r'[\u4e00-\u9fff]+', lambda match: building_code, input_text)
    
    return input_text

def generate_promotion_data(organizer, time, location, event_name, description, fee):
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""
    使用以下資料生中文及英文宣傳文宣，先是中文，後面才是英文：
    要包含以下內容
    主辦單位: {organizer}
    活動時間: {time}
    活動地點: {location}
    活動名稱: {event_name}
    活動內容: {description}
    費用: {fee}
    以及一個有趣的笑話，才能吸引大家參加
    生成後輸出文案。
    """
    response = model.generate_content(prompt)
    return response.text  

