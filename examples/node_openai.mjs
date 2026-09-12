// The same two-line change in JavaScript.
//
//   npm install openai
//   export OPENMETRIC_KEY=om_live_...
//   node examples/node_openai.mjs

import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8099/v1",       // was https://openrouter.ai/api/v1
  apiKey: process.env.OPENMETRIC_KEY,        // was process.env.OPENROUTER_API_KEY
  defaultHeaders: {
    "X-OpenMetric-Project": "my-node-app",
    "X-OpenMetric-Use-Case": "chat",
  },
});

const response = await client.chat.completions.create({
  model: "openai/gpt-4o-mini",
  messages: [{ role: "user", content: "Give me one tip for naming variables." }],
});

console.log(response.choices[0].message.content);
console.log("\nDashboard: http://localhost:8099/");
