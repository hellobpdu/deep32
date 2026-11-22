"""What a follow-up question's agent is told of the conversation: labelled earlier turns, cut to fit the input cap."""
import json

from support import OfflineCase
from deep32_relay import native

Q = native.EARLIER_QUESTION
A = native.EARLIER_ANSWER
N = native.NEW_QUESTION


class Conversation(OfflineCase):
    def test_the_labels_are_the_decided_ones(self):
        self.assertEqual(Q, 'Earlier question:')
        self.assertEqual(A, 'Earlier answer (its quotations were checked in that turn; check again any you reuse):')
        self.assertEqual(N, 'New question:')

    def test_a_first_question_stands_alone(self):
        self.assertEqual(native._conversation('synthetic question', ()), 'synthetic question')
        self.assertEqual(native._prompt('INSTRUCTION', 'synthetic question', ()), 'INSTRUCTION\n\nsynthetic question')

    def test_earlier_turns_are_labelled_oldest_first(self):
        text = native._conversation('q3', ('q1', 'a1', 'q2', 'a2'))
        self.assertEqual(text, '\n\n'.join((Q + '\nq1', A + '\na1', Q + '\nq2', A + '\na2', N + '\nq3')))
        self.assertEqual(native._prompt('INSTRUCTION', 'q3', ['q1', 'a1']),
                         'INSTRUCTION\n\n' + Q + '\nq1\n\n' + A + '\na1\n\n' + N + '\nq3')

    def test_an_odd_history_is_passed_on_unlabelled(self):
        self.assertEqual(native._conversation('q', ('one item',)), 'one item\n\nq')

    def test_a_wrong_type_is_refused(self):
        for question, context in ((None, ()), ('q', 'not a sequence'), (7, []), ('q', None)):
            with self.subTest(question=question), self.assertRaisesRegex(native.NativeError, '^native-input-limit$'):
                native._conversation(question, context)

    def claude(self, instruction='INSTRUCTION'):
        protocol = native.ClaudeProtocol(self.root, 'synthetic-model', instruction)
        protocol.stage = 'pre-turn'
        return protocol

    def codex(self, instruction='INSTRUCTION'):
        protocol = native.CodexProtocol(self.root, None, None, instruction)
        protocol.thread = 'synthetic-thread'
        return protocol

    def test_both_agents_get_the_labelled_history(self):
        history = ('q1', 'a1')
        content = json.loads(self.claude().turn('q2', history))['message']['content']
        self.assertEqual(content, 'INSTRUCTION\n\n' + native._conversation('q2', history))
        text = json.loads(self.codex().turn('q2', history))['params']['input'][0]['text']
        self.assertEqual(text, native._conversation('q2', history))

    def test_a_history_over_the_cap_loses_its_oldest_turns_and_the_turn_still_starts(self):
        instruction = 'I' * 200000
        history = []
        for index in range(6):                      # six exchanges of about 100,000 bytes each
            history += ['question %d' % index, ('answer %d ' % index) + 'x' * 100000]
        line = self.claude(instruction).turn('the new question', tuple(history))
        self.assertLessEqual(len(line), native.MAX_INPUT)
        content = json.loads(line)['message']['content']
        self.assertTrue(content.startswith(instruction + '\n\n' + Q + '\n'))
        self.assertTrue(content.endswith(N + '\nthe new question'))
        kept = [index for index in range(6) if 'question %d' % index in content]
        self.assertEqual(kept, [3, 4, 5])           # the newest that fit: 200,000 + 3 x 100,000 is under 512 KiB
        text = json.loads(self.codex(instruction).turn('the new question', tuple(history)))['params']['input'][0]['text']
        self.assertEqual([index for index in range(6) if 'question %d' % index in text], [1, 2, 3, 4, 5])

    def test_escaping_counts_against_the_cap(self):
        # a quote character costs two bytes in the JSON line: 150,000 of them are 300,000 bytes
        history = ('q1', '"' * 150000, 'q2', '"' * 150000)
        content = json.loads(self.claude('I' * 100000).turn('q3', history))['message']['content']
        self.assertNotIn(Q + '\nq1', content)
        self.assertIn(Q + '\nq2', content)

    def test_a_question_that_cannot_fit_even_alone_is_refused(self):
        with self.assertRaisesRegex(native.NativeError, '^native-input-limit$'):
            self.claude('I' * native.MAX_INPUT).turn('q', ('q1', 'a1'))

    def test_the_stage_is_untouched_when_the_turn_is_refused(self):
        protocol = self.claude('I' * native.MAX_INPUT)
        with self.assertRaises(native.NativeError):
            protocol.turn('q', ())
        self.assertEqual(protocol.stage, 'pre-turn')
