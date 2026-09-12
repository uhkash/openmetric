"""The whole integration: change the base URL and the key.

pip install openai
export OPENMETRIC_KEY=om_live_...
python examples/openai_sdk.py
"""

import os

from openai import OpenAI

client = OpenAI(
    # Was: https://openrouter.ai/api/v1  (or https://api.openai.com/v1)
    base_url="http://localhost:8099/v1",
    # Was: os.environ["OPENROUTER_API_KEY"] - your app no longer holds a provider key.
    api_key=os.environ["OPENMETRIC_KEY"],
)

response = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Summarise the plot of Dune in one sentence."}],
)

print(response.choices[0].message.content)
print("\nThat call is now in your dashboard: http://localhost:8099/")
