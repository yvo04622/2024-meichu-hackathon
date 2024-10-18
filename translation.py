import vertexai
from google.cloud import translate_v2 as translate
from vertexai.generative_models import GenerativeModel, Part, SafetySetting

YOUR_PROJECT_NAME = "anan-meichu"


def translate_text(text, target_language="zh"):
    translate_client = translate.Client()
    if isinstance(text, bytes):
        text = text.decode("utf-8")

    result = translate_client.translate(text, target_language=target_language)
    print(result.keys())
    print("Text: {}".format(result["input"]))
    print("Translation: {}".format(result["translatedText"]))
    print("Detected source language: {}".format(result["detectedSourceLanguage"]))
    translated_text = result["translatedText"]
    # change the encoding to utf-8
    translated_text = translated_text.encode("utf-8")

    # save result
    with open("translation.txt", "w", encoding="UTF-8") as f:
        f.write(result["translatedText"])

    return result


def translate_text_from_vertexAI(text, project_name, model_name="gemini-1.5-flash"):
    vertexai.init(project=project_name, location="us-central1")
    model = GenerativeModel(model_name)
    responses = model.generate_content(
        [f"Translate the following text to Traditional Chinese.\n{text}"],
        generation_config={
            "max_output_tokens": 2000,
            "temperature": 0.0,
            "top_p": 1.0,
        },
    )

    return responses.candidates[0].text


def main(text_file):
    with open(text_file, "r") as f:
        text = f.read()

    result = translate_text_from_vertexAI(text, project_name=YOUR_PROJECT_NAME)
    output_file = text_file.replace(".txt", "_translated.txt")
    with open(output_file, "w", encoding="UTF-8") as f:
        f.write(result)


if __name__ == "__main__":
    main("audios/en_stats.txt")
