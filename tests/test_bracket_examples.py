import unittest

from coach.java import run_java
from coach.problems import BUILTINS


class BracketExamplesTest(unittest.TestCase):
    def test_builtin_cases_with_real_java_stack_solution(self):
        problem = next(p for p in BUILTINS if p['id'] == 'brackets')
        code = '''import java.util.ArrayDeque;
class Solution {
    public boolean solution(String text) {
        ArrayDeque<Character> expected = new ArrayDeque<>();
        for (char c : text.toCharArray()) {
            switch (c) {
                case '(' -> expected.push(')');
                case '[' -> expected.push(']');
                case '{' -> expected.push('}');
                default -> {
                    if (expected.isEmpty() || expected.pop() != c) return false;
                }
            }
        }
        return expected.isEmpty();
    }
}'''
        result = run_java(code, problem, submit=True)
        self.assertEqual((result['status'], result['passed'], result['total']),
                         ('passed', 9, 9), result)  # 7 examples + 2 large hidden cases
        self.assertTrue(all(t.get('time_ms') is not None for t in result['tests']))
        self.assertEqual(result['tests'][0]['args'], ['([]){}'])
        self.assertIs(result['tests'][0]['actual'], True)


if __name__ == '__main__':
    unittest.main()
