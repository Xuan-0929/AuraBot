import json
import unittest
from io import BytesIO

from backend.agent.LLMClient import LLMClient
from backend.agent import LLMClient as ExportedLLMClient


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        if isinstance(self.content, bytes):
            return self.content

        return json.dumps(self.content).encode("utf-8")

    def __iter__(self):
        if isinstance(self.content, list):
            return iter(self.content)

        return iter(BytesIO(self.read()))


class FakeLLMClient(LLMClient):
    def __init__(self, response, **kwargs):
        super().__init__(**kwargs)
        self.response = response
        self.requests = []

    def _post(self, body):
        self.requests.append(
            {
                "url": self._chat_completions_url(),
                "headers": self._headers(),
                "body": body,
            }
        )
        return FakeResponse(self.response)


class LLMClientTests(unittest.TestCase):
    def test_exports_llm_client_from_agent_package(self):
        self.assertIs(ExportedLLMClient, LLMClient)

    def test_invoke_posts_openai_compatible_non_streaming_request(self):
        client = FakeLLMClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": "hello from model",
                        }
                    }
                ]
            },
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
            stream=False,
        )

        result = client.invoke([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "hello from model")
        self.assertFalse(hasattr(client, "complete"))
        self.assertEqual(len(client.requests), 1)
        request = client.requests[0]
        self.assertEqual(request["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-key")
        body = json.loads(request["body"].decode("utf-8"))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])
        self.assertNotIn("stream", body)

    def test_streaming_invoke_posts_streaming_request_and_returns_content(self):
        client = FakeLLMClient(
            [
                b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
                b"data: [DONE]\n\n",
            ],
            base_url="https://api.example.com/v1/",
            api_key="test-key",
            model="test-model",
            stream=True,
        )

        result = client.invoke([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "hello")
        body = json.loads(client.requests[0]["body"].decode("utf-8"))
        self.assertTrue(body["stream"])

    def test_raises_when_non_streaming_response_has_no_content(self):
        client = FakeLLMClient(
            {"choices": [{"message": {"content": ""}}]},
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
            stream=False,
        )

        with self.assertRaisesRegex(ValueError, "did not include content"):
            client.invoke([{"role": "user", "content": "hello"}])

    def test_raises_when_streaming_response_has_no_content(self):
        client = FakeLLMClient(
            [b"data: [DONE]\n\n"],
            base_url="https://api.example.com/v1",
            api_key="test-key",
            model="test-model",
            stream=True,
        )

        with self.assertRaisesRegex(ValueError, "did not include content"):
            client.invoke([{"role": "user", "content": "hello"}])


if __name__ == "__main__":
    unittest.main()
