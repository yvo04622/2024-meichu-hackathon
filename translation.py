import os

import vertexai
from vertexai.generative_models import GenerativeModel, Part, SafetySetting

YOUR_PROJECT_NAME = os.getenv("PROJECT_NAME")


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


def main(text, output_file):
    result = translate_text_from_vertexAI(text, project_name=YOUR_PROJECT_NAME)
    return result


if __name__ == "__main__":
    main("audios/en_stats.txt")
