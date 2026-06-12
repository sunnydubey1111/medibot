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

def call_llm(prompt: str, system_instruction: str = None) -> str:
    """
    Calls Google Gemini (gemini-3.5-flash) with the given prompt and system instruction.
    If the API key is missing, returns a mock response to allow graceful operation.
    """
    if not api_key:
        return "[MOCK RESPONSE] GEMINI_API_KEY is not set. Please configure it in your .env file."
        
    try:
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_instruction
        )
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return "Oops! I'm having trouble connecting to my system right now. Please check your internet connection and try again. If the issue continues, please contact the hospital IT Helpdesk."
