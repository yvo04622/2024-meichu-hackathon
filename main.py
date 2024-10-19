import json
import logging
import os
import sys
from utils import replace_location_with_abbrev, generate_promotion_data

if os.getenv("API_ENV") != "production":
    from dotenv import load_dotenv

    load_dotenv()


from fastapi import FastAPI, HTTPException, Request
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ReplyMessageRequest,
    TextMessage,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, AccountLinkEvent

import uvicorn
from fastapi.responses import RedirectResponse

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


@handler.add(AccountLinkEvent, reply_message=TextMessageContent)
def push_quick_message(event):
    # loging part
    logging.info(event)
    text = event.message.text
    user_id = event.source.user_id

    fdb = firebase.FirebaseApplication(firebase_url, None)
    user_chat_path = f"chat/{user_id}"
    user_state_path = f"state/{user_id}"

    conversation_data = fdb.get(user_chat_path, None)
    if conversation_data is None:
        messages = []
    else:
        messages = conversation_data

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text="Quick reply",
                        quick_reply=QuickReply(
                            items=[QuickReplyItem(action=MessageAction(label="生成文案", text="\\slogan"))]
                        ),
                    )
                ],
            )
        )

    return "OK"


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
    # user_state = fdb.get(user_state_path, None)

    if conversation_data is None:
        messages = []
    else:
        messages = conversation_data

    if text == "C":
        fdb.delete(user_chat_path, None)
        fdb.delete(user_state_path, None)
        reply_msg = "已清空對話紀錄"
    elif text.startswith("\\slogan"):
        parts = text.split(" ", 6)

        if len(parts) < 7:
            reply_msg = "輸入不完整，請依次輸入：\\poster 主辦單位 時間 地點 活動名稱 活動內容 費用"
        else:
            _, organizer, time, location, event_name, description, fee = parts
            fdb.put_async(user_chat_path, "organizer", organizer)
            fdb.put_async(user_chat_path, "time", time)
            fdb.put_async(user_chat_path, "location", location)
            fdb.put_async(user_chat_path, "event_name", event_name)
            fdb.put_async(user_chat_path, "description", description)
            fdb.put_async(user_chat_path, "fee", fee)

            location = replace_location_with_abbrev(location)

            reply_msg = "開始文宣，請稍等..."

            event_text = generate_promotion_data(organizer, time, location, event_name, description, fee)

            reply_msg = f"文宣內容: {event_text}"

    else:
        reply_msg = "請輸入有效命令，例如：\\poster 主辦單位 時間 地點 活動名稱 活動內容 費用"

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
