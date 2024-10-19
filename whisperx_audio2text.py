import os
import tempfile

from faster_whisper import WhisperModel
from opencc import OpenCC
from pydub import AudioSegment

device = "cpu"
batch_size = 16  # reduce if low on GPU mem
compute_type = "int8"  # change to "int8" if low on GPU mem (may reduce accuracy)
hf_api_key = os.getenv("HUGGINGFACE_API_KEY")


def load_model(size="tiny", device="cpu", compute_type="int8"):
    model = WhisperModel(size, device, compute_type=compute_type)
    return model


def m4a_to_mp3(m4a_file):
    audio = AudioSegment.from_file(m4a_file, format="m4a")
    output_file = m4a_file.replace(".m4a", ".mp3")
    return audio.export(output_file, format="mp3")


def main(audio_file):
    # with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_audio_file:
    #     temp_audio_file.write(audio_file)
    #     audio_file = temp_audio_file.name

    model = load_model("base", device, compute_type=compute_type)
    segments, info = model.transcribe(audio_file, beam_size=5)
    language = info.language

    result_text = ""
    for segment in segments:
        result_text += segment.text + "\n"

    # convert to traditional Chinese
    cc = OpenCC("s2t")
    result_text = cc.convert(result_text)

    return segments, result_text, language


if __name__ == "__main__":
    audio_file = "audios/en_stats.mp3"
    conv_json, text, language = main(audio_file)
