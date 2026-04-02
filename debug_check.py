import google.generativeai as genai
genai.configure(api_key="AIzaSyA8k3eFNqEaY_xTup3xQMPLC0Gl0zaF6n0")

for m in genai.list_models():
    print(m.name)