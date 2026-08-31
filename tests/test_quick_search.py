import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
from gpt_researcher.agent import GPTResearcher
import os

class TestQuickSearch(unittest.TestCase):

    # EMBEDDING 现在是 zhipuai:embedding-3,缺 ZHIPUAI_API_KEY 时会明确报错。
    # 这两个用例本来就不该需要真实凭据(下面 OpenAIEmbeddings 被 patch 掉了),
    # 但报错发生在构造之前,patch 拦不住 —— 所以补一个假 key。
    @patch.dict(os.environ, {"ZHIPUAI_API_KEY": "test-key-not-used"})
    @patch('gpt_researcher.agent.get_search_results', new_callable=AsyncMock)
    @patch('gpt_researcher.agent.create_chat_completion', new_callable=AsyncMock)
    @patch('langchain_openai.OpenAIEmbeddings')
    def test_quick_search_no_summary(self, mock_embeddings, mock_create_chat, mock_search):
        # Setup mocks
        mock_search.return_value = [{'title': 'Test Result', 'content': 'Content', 'url': 'http://test.com'}]

        # Initialize researcher with dummy config to avoid API key issues
        researcher = GPTResearcher(query="test query")

        # Run quick_search without summary
        results = asyncio.run(researcher.quick_search("test query", aggregated_summary=False))

        # Verify
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Test Result')
        mock_create_chat.assert_not_called()

    # EMBEDDING 现在是 zhipuai:embedding-3,缺 ZHIPUAI_API_KEY 时会明确报错。
    # 这两个用例本来就不该需要真实凭据(下面 OpenAIEmbeddings 被 patch 掉了),
    # 但报错发生在构造之前,patch 拦不住 —— 所以补一个假 key。
    @patch.dict(os.environ, {"ZHIPUAI_API_KEY": "test-key-not-used"})
    @patch('gpt_researcher.agent.get_search_results', new_callable=AsyncMock)
    @patch('gpt_researcher.agent.create_chat_completion', new_callable=AsyncMock)
    @patch('langchain_openai.OpenAIEmbeddings')
    def test_quick_search_with_summary(self, mock_embeddings, mock_create_chat, mock_search):
        # Setup mocks
        mock_search.return_value = [{'title': 'Test Result', 'content': 'Content', 'url': 'http://test.com'}]
        mock_create_chat.return_value = "This is a summary."

        # Initialize researcher
        researcher = GPTResearcher(query="test query")

        # Run quick_search with summary
        summary = asyncio.run(researcher.quick_search("test query", aggregated_summary=True))

        # Verify
        self.assertEqual(summary, "This is a summary.")
        mock_create_chat.assert_called_once()

if __name__ == '__main__':
    unittest.main()
