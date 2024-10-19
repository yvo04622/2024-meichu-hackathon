import json
import logging
import os
import re
import urllib
from io import BytesIO

import google.generativeai as genai
import requests
from PIL import Image

campus_json = json.load(open("campus.json"))

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


def check_image(url=None, b_image=None):
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

            input_text = re.sub(
                r"[\u4e00-\u9fff]+", lambda match: building_code, input_text
            )

    return input_text


def generate_promotion_data(organizer, time, location, event_name, description, fee):
    model = genai.GenerativeModel("gemini-1.5-flash")
    # model = genai.GenerativeModel("gemini-1.5-pro")
    # imagen = genai.ImageGenerationModel("imagen-3.0-generate-001")

    prompt = f"""
    使用以下資料生中文及英文宣傳文宣，先是中文，後面才是英文：
    一定要包含以下內容: 
    主辦單位: {organizer}
    活動時間: {time}
    中文文宣活動地點: {location}而英文的活動地點則是: {replace_location_with_abbrev(location)}
    活動名稱: {event_name}
    活動內容: {description}
    費用: {fee}
    文案的部分，
    如果費用為零，請強調活動完全免費，
    強調大家可以學到或體驗到什麼東西，
    以及一個相關的有趣的笑話，才能吸引大家參加，
    生成後輸出文案。
    """

    response = model.generate_content(prompt)
    return response.text


def speech_translate_summary(audio_file=None, bimg=None):
    from whisperx_audio2text import main as audio2text

    from translation import main as translate

    print("audio2text...")
    conv_json, text, language = audio2text(audio_file)

    print("done. translate...")
    translated_text = translate(text, language)

    image = None
    if bimg is not None:
        image = Image.open(BytesIO(bimg))

    model = genai.GenerativeModel("gemini-1.5-flash")
    if image is None:
        prompt = f"根據以下課程逐字稿。撰寫一份本課程的重點筆記。\n重點筆記應以markdown格式撰寫，且不可超過20行。\n課程逐字稿：\n{translated_text}"
        response = model.generate_content([prompt])
    elif audio_file is None:
        prompt = f"根據課程的相關圖片。撰寫一份本課程的重點筆記。\n重點筆記應以markdown格式撰寫，且不可超過20行。"
        response = model.generate_content([prompt, image])
    else:
        prompt = f"根據以下課程逐字稿及相關圖片。撰寫一份本課程的重點筆記。\n重點筆記應以markdown格式撰寫，且不可超過20行。\n課程逐字稿：\n{translated_text}"
        response = model.generate_content([prompt, image])

    logger.info(response.text)

    return response.text
