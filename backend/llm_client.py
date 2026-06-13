import os
import sys
from dotenv import load_dotenv
import google.generativeai as genai

# Ensure stdout uses UTF-8 to avoid encoding errors on Windows
sys.stdout.reconfigure(encoding='utf-8')

# Load env variables from workspace root .env
current_dir = os.path.dirname(os.path.abspath(__file__))
workspace_root = os.path.dirname(current_dir)
load_dotenv(os.path.join(workspace_root, ".env"))

api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    print("WARNING: GEMINI_API_KEY is not set in the environment or .env file.")

def stream_llm(prompt: str, system_instruction: str = None, history: list = None):
    """
    Synchronous generator — yields text chunks from Gemini as they arrive.
    Safe to run in a thread pool alongside an async event loop.
    """
    if not api_key:
        yield "[MOCK RESPONSE] GEMINI_API_KEY is not set. Please configure it in your .env file."
        return
    try:
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_instruction,
        )
        if history:
            chat = model.start_chat(history=history)
            response = chat.send_message(prompt, stream=True)
        else:
            response = model.generate_content(prompt, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text
    except Exception as e:
        print(f"Error streaming from Gemini API: {e}")
        yield "Oops! I'm having trouble connecting to my system right now. Please check your internet connection and try again."


def call_llm(prompt: str, system_instruction: str = None, history: list = None) -> str:
    """
    Calls Google Gemini (gemini-3.5-flash) with the given prompt and optional conversation history.
    history format: [{"role": "user", "parts": ["..."]}, {"role": "model", "parts": ["..."]}]
    If the API key is missing, returns a mock response to allow graceful operation.
    """
    if not api_key:
        return "[MOCK RESPONSE] GEMINI_API_KEY is not set. Please configure it in your .env file."

    try:
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_instruction
        )
        if history:
            chat = model.start_chat(history=history)
            response = chat.send_message(prompt)
        else:
            response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return "Oops! I'm having trouble connecting to my system right now. Please check your internet connection and try again. If the issue continues, please contact the hospital IT Helpdesk."
