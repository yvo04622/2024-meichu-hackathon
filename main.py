import json
import logging
import os
import sys

from utils import (
    generate_promotion_data,
    replace_location_with_abbrev,
    speech_translate_summary,
    make_form,
    shorten_url_by_reurl_api,
)

if os.getenv("API_ENV") != "production":
    from dotenv import load_dotenv

    load_dotenv()

import tempfile

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessageAction,
    MessagingApi,
    MessagingApiBlob,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
    PushMessageRequest,
)
from linebot.v3.webhooks import (
    AccountLinkEvent,
    AudioMessageContent,
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
)
import requests
from urllib.parse import urlencode
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as Req
from googleapiclient.discovery import build

logging.basicConfig(level=os.getenv("LOG", "WARNING"))
logger = logging.getLogger(__file__)

app = FastAPI()

channel_secret = os.getenv("LINE_CHANNEL_SECRET", None)
channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", None)
if channel_secret is None:
    print("Specify LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)
if channel_access_token is None:
    print("Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.")
    sys.exit(1)

configuration = Configuration(access_token=channel_access_token)

handler = WebhookHandler(channel_secret)


import google.generativeai as genai
from firebase import firebase
from utils import check_image, create_gcal_url, is_url_valid, shorten_url_by_reurl_api

firebase_url = os.getenv("FIREBASE_URL")
gemini_key = os.getenv("GEMINI_API_KEY")
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
redirect_uri = os.getenv("REDIRECT_URI")

# 設定 OAuth 2.0 參數
scope = "https://www.googleapis.com/auth/forms.body https://www.googleapis.com/auth/drive"
auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'scope': scope, 'response_type': 'code', 'access_type': 'offline', 'prompt': 'consent'})}"


CS_begin = False
CS_audio = None
CS_pdf = None
form_begin = False
authorization_code = None
access_token = None
refresh_token = None

# Initialize the Gemini Pro API
genai.configure(api_key=gemini_key)


def exchange_code_for_token(code: str):
    """交換授權碼換取存取權杖"""
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = requests.post(token_url, data=payload)

    return response.json()


@app.get("/health")
async def health():
    return "ok"


@app.get("/")
async def find_image_keyword(img_url: str):
    image_data = check_image(img_url)
    image_data = json.loads(image_data)

    g_url = create_gcal_url(
        image_data["title"],
        image_data["time"],
        image_data["location"],
        image_data["content"],
    )
    if is_url_valid(g_url):
        return RedirectResponse(g_url)
    else:
        return "Error"


@app.post("/webhooks/line")
async def handle_callback(request: Request):
    signature = request.headers["X-Line-Signature"]

    # get request body as text
    body = await request.body()
    body = body.decode()

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    global CS_begin, CS_audio, CS_pdf, form_begin, authorization_code, access_token, refresh_token
    # loging part
    logging.info(event)
    text = event.message.text
    user_id = event.source.user_id

    fdb = firebase.FirebaseApplication(firebase_url, None)
    user_chat_path = f"chat/{user_id}"
    user_state_path = f"state/{user_id}"

    conversation_data = fdb.get(user_chat_path, None)
    user_state = fdb.get(user_state_path, None)

    if conversation_data is None:
        messages = []
    else:
        messages = conversation_data

    if text == "C":
        fdb.delete(user_chat_path, None)
        fdb.delete(user_state_path, None)
        reply_msg = "已清空對話紀錄"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )
    elif text == "選項":
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text="Quick reply",
                            quick_reply=QuickReply(
                                items=[
                                    QuickReplyItem(action=MessageAction(label="語音轉表單", text="\\form")), 
                                    QuickReplyItem(action=MessageAction(label="簡報轉摘要", text="\\pdfnote")), 
                                    QuickReplyItem(action=MessageAction(label="語音轉摘要", text="\\audnote")), 
                                    QuickReplyItem(action=MessageAction(label="生成文案", text="\\slogan")),
                                ]
                            ),
                        )
                    ],
                )
            )
    elif text == "\\slogan":
        reply_msg = "請依序輸入並以空白鍵隔開：主辦單位 時間 地點 活動名稱 活動內容 費用"
        fdb.put_async(user_state_path, None, {"step": "awaiting_keyword"})
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )
    elif text == "\\audnote":
        CS_begin = True
        reply_msg = "好的，請給我課程的錄音檔！"
        # fdb.put_async(user_state_path, None, {"step": "awaiting_audio"})
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )
    elif text == "\\pdfnote":
        CS_begin = True
        reply_msg = "好的，請給我課程相關的截圖或圖片！"
        # fdb.put_async(user_state_path, None, {"step": "awaiting_pdf"})
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )
    elif text == "\\form":
        form_begin = True
        reply_msg = "好的，請給我音檔"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )

    elif CS_begin and text == "n":
        if CS_audio is None and CS_pdf is None:
            reply_msg = "想整理社課筆記的話，請先提供錄音檔或圖片！"
        else:
            summary = speech_translate_summary(CS_audio, CS_pdf)
            CS_begin = False
            CS_audio = None
            CS_pdf = None
            reply_msg = summary
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )

    elif user_state["step"] == "awaiting_keyword":
        # 收集到關鍵字，要求輸入主題1
        fdb.delete(user_state_path, None)
        reply_msg = "開始生成文宣，請稍等..."
        parts = text.split(" ", 5)
        organizer, time, location, event_name, description, fee = parts
        fdb.put_async(user_chat_path, "organizer", organizer)
        fdb.put_async(user_chat_path, "time", time)
        fdb.put_async(user_chat_path, "location", location)
        fdb.put_async(user_chat_path, "event_name", event_name)
        fdb.put_async(user_chat_path, "description", description)
        fdb.put_async(user_chat_path, "fee", fee)
        event_text = generate_promotion_data(organizer, time, location, event_name, description, fee)

        reply_msg = f"文宣內容: {event_text}"
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )

    else:
        reply_msg = "請輸入有效命令，例如： 主辦單位 時間 地點 活動名稱 活動內容 費用"

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg)],
                )
            )

    return "OK"


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_img_message(event):
    image_content = b""
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        image_content = line_bot_blob_api.get_message_content(event.message.id)

    global CS_begin, CS_pdf, CS_audio

    if CS_begin and CS_audio is not None:
        CS_pdf = image_content
        summary = speech_translate_summary(CS_audio, CS_pdf)
        CS_begin = False
        CS_audio = None
        CS_pdf = None
        reply_msg = summary
    elif CS_begin:
        CS_pdf = image_content
        reply_msg = '已收到圖片，如果有的話，請給我課程的錄音檔！\n如果沒有，請輸入"n"告訴我～'
    else:
        reply_msg = "你想做什麼呢？如果想整理社課筆記，請先點選「上傳圖片」！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(replyToken=event.reply_token, messages=[TextMessage(text=reply_msg)])
        )
    return "OK"


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    global CS_begin, CS_audio, CS_pdf, form_begin, access_token, refresh_token
    if form_begin:
        user_id = event.source.user_id
        if not access_token:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=event.reply_token,
                        messages=[TextMessage(text=f"請點擊以下連結進行授權：{shorten_url_by_reurl_api(auth_url)}")],
                    )
                )

            response = requests.get("https://685d-60-251-196-41.ngrok-free.app/get_token")
            # 檢查是否成功取得授權碼
            if response.status_code == 200:
                authorization_code = response.json().get("authorization_code")
                if not authorization_code:
                    raise Exception("Authorization code not found in response.")
            else:
                raise Exception(f"Failed to get authorization code: {response.text}")

            # 用授權碼交換存取權杖
            token_data = exchange_code_for_token(authorization_code)

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

            if not access_token or not refresh_token:
                raise ValueError("Access or refresh token missing.")

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/forms.body", "https://www.googleapis.com/auth/drive"],
        )
        try:
            creds.refresh(Req())
        except Exception as e:
            print(f"Failed to refresh token: {e}")

        # 使用憑證初始化 form_service 物件
        global form_service
        form_service = build("forms", "v1", credentials=creds, static_discovery=False)

        # 下載語音訊息檔案
        audio_message_id = event.message.id

        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            audio_content = line_bot_blob_api.get_message_content(audio_message_id)

        # 將 M4A 轉成 MP3
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_audio_file:
            temp_audio_file.write(audio_content)
            mp3_path = temp_audio_file.name

        # 發送語音檔案給 Gemini API，回傳表單連結
        form_url = make_form(mp3_path, form_service, access_token)
        reply_msg = shorten_url_by_reurl_api(form_url)

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=reply_msg)]))

    if CS_begin:
        with ApiClient(configuration) as api_client:
            line_bot_blob_api = MessagingApiBlob(api_client)
            audio_content = line_bot_blob_api.get_message_content(event.message.id)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_audio_file:
            temp_audio_file.write(audio_content)
            audio_content = temp_audio_file.name

    if CS_begin and CS_pdf is not None:
        CS_audio = audio_content
        summary = speech_translate_summary(CS_audio, CS_pdf)
        CS_begin = False
        CS_audio = None
        CS_pdf = None
        reply_msg = summary
    elif CS_begin:
        CS_audio = audio_content
        reply_msg = '已收到錄音檔，如果有的話，請給我課程相關的截圖或圖片！\n如果沒有，請輸入"n"告訴我～'  # \n(目前尚未支援上傳pdf檔，請輸入任意字元繼續)
    else:
        reply_msg = "你想做什麼呢？如果想整理社課筆記，請先點選「上傳音檔」！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_msg)],
            )
        )

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", default=8080))
    debug = True if os.environ.get("API_ENV", default="develop") == "develop" else False
    logging.info("Application will start...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=debug)
