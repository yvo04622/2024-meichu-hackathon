import json
import logging
import os
import sys

from utils import (
    generate_promotion_data,
    replace_location_with_abbrev,
    speech_translate_summary,
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
)
from linebot.v3.webhooks import (
    AccountLinkEvent,
    AudioMessageContent,
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
)

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

CS_begin = False
CS_audio = None
CS_pdf = None

# Initialize the Gemini Pro API
genai.configure(api_key=gemini_key)


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
                                    QuickReplyItem(
                                        action=MessageAction(
                                            label="生成文案", text="\\slogan"
                                        )
                                    )
                                ]
                            ),
                        )
                    ],
                )
            )
    elif text == "\\slogan":
        reply_msg = "請依次輸入：\\poster 主辦單位 時間 地點 活動名稱 活動內容 費用"
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
        reply_msg = "開始文宣，請稍等..."
        parts = text.split(" ", 5)
        organizer, time, location, event_name, description, fee = parts
        fdb.put_async(user_chat_path, "organizer", organizer)
        fdb.put_async(user_chat_path, "time", time)
        fdb.put_async(user_chat_path, "location", location)
        fdb.put_async(user_chat_path, "event_name", event_name)
        fdb.put_async(user_chat_path, "description", description)
        fdb.put_async(user_chat_path, "fee", fee)
        event_text = generate_promotion_data(
            organizer, time, location, event_name, description, fee
        )

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
def handle_note_img_message(event):
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
        reply_msg = (
            '已收到圖片，如果有的話，請給我課程的錄音檔！\n如果沒有，請輸入"n"告訴我～'
        )
    else:
        reply_msg = "你想做什麼呢？如果想整理社課筆記，請先點選「上傳圖片」！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                replyToken=event.reply_token, messages=[TextMessage(text=reply_msg)]
            )
        )
    return "OK"


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_note_audio_message(event):
    global CS_begin, CS_audio
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
