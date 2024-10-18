import os

import whisperx
from pydub import AudioSegment

device = "cpu"
batch_size = 16  # reduce if low on GPU mem
compute_type = "int8"  # change to "int8" if low on GPU mem (may reduce accuracy)
hf_api_key = os.getenv("HUGGINGFACE_API_KEY")


def load_model(size="tiny", device="cpu", compute_type="int8"):
    return whisperx.load_model(size, device, compute_type=compute_type)


def m4a_to_mp3(m4a_file):
    audio = AudioSegment.from_file(m4a_file, format="m4a")
    output_file = m4a_file.replace(".m4a", ".mp3")
    return audio.export(output_file, format="mp3")


def post_process(result):
    speech = []
    all_text = ""
    speakers = []
    for segment in result["segments"]:
        speaker = segment["speaker"]
        if speaker not in speakers:
            speakers.append(speaker)
        text = segment["text"]
        speech.append({"speaker": speaker, "text": text})

    for segment in speech:
        if len(speakers) <= 1:
            all_text += f"{segment['text']}\n"

    return speech, all_text


def main(audio_file):
    if audio_file.endswith(".m4a"):
        audio_file = m4a_to_mp3(audio_file)

    # model size depends on the available GPU memory of your machine
    model = load_model("tiny", device, compute_type=compute_type)
    audio = whisperx.load_audio(audio_file)
    print("Transcribing...")
    result = model.transcribe(audio, batch_size=batch_size)
    language = result["language"]

    print("Aligning...")
    model_a, metadata = whisperx.load_align_model(
        language_code=result["language"], device=device
    )
    result = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )

    print("Assigning speakers...")
    diarize_model = whisperx.DiarizationPipeline(
        use_auth_token=hf_api_key, device=device
    )

    # add min/max number of speakers if known
    diarize_segments = diarize_model(audio)
    # diarize_model(audio, min_speakers=min_speakers, max_speakers=max_speakers)

    result = whisperx.assign_word_speakers(diarize_segments, result)

    conv_json, text = post_process(result)
    print(text)
    with open("audios/en_stats.txt", "w") as f:
        f.write(text)
    return conv_json, text, language  # return json and all plain text


if __name__ == "__main__":
    audio_file = "audios/en_stats.mp3"
    conv_json, text, language = main(audio_file)
