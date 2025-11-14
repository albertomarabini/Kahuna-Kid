# Notes for the tester:

## **⚠⚠⚠IF YOU WERE GIVEN ACCESS TO HIGHER MODELS PLEASE DO NOT OVERUSE THE TOOL⚠⚠⚠**
- Understand that this tool can make a huge amount of calls: if you were given access to higher models (Eg: gemini 2.5 pro) **understand that a single call could get extremely expensive. I beg you to use it wisely!**

## **This tool is not a chat!**
- Although it will always do its best to give you an answer, it is designed to take entire software requirements document in input.
- As output it will generate a zip file!
- So please be careful in the way you use it. Within the `Test Prompts` folder there are examples of potential prompts you can take inspiration from.
- You are fully encouraged to come up with your own, just understand the actual usage of the tool

## Resilience:
- At times the Vertex AI backend might experience some difficulty and will not respond immediately.
- If you see the UI stuck, probably is the backend fighting through timeouts: It will take a total of 9 retries before the server gives up (3 on a specific query * 3 single agent retry)
- The server itself is pretty shielded from service failures (only one job can be executed at a time)
- If the backend fails **you are gonna be notified on the UI**, but an hanging spinner doesn't necessarily mean that the process itself has failed.
- Is poor UI? Sure. But this is a POC, not a production system.
