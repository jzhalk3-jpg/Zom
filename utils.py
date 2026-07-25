import re

def extract_links(text):
    pattern = r"(?:https?://)?(?:t\.me/|telegram\.me/)([a-zA-Z0-9_]+|joinchat/[a-zA-Z0-9_-]+|\+[a-zA-Z0-9_-]+)"
    return re.findall(pattern, text)
