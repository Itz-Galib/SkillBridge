"""Seed a deterministic, synthetic GraphRAG assessment bank for local/staging demos.

Run from backend/: python seed_graphrag_questions.py
Safe to re-run: fixed IDs + ON CONFLICT DO NOTHING. Does not create users or touch candidate data.
"""
import json
import time

ACTOR = "SkillBridge synthetic GraphRAG demo seed"
NOW = time.time()

# Fundamentals: exact topic/difficulty matrix (25 questions).
FUNDAMENTALS = [
# DSA (5): 2 Easy, 2 Medium, 1 Hard
("DSA","CAT-02","Easy","MCQ","Which data structure follows LIFO order?","Stack",["Queue","Stack","Tree","Graph"],"A stack removes the most recently added item first.","", "Recognize stack behavior.",60),
("DSA","CAT-02","Easy","MCQ","Which data structure is commonly used for breadth-first search in a graph?","Queue",["Stack","Queue","Heap","Hash map"],"BFS explores nodes level by level using a queue.","","Explain the queue-based BFS traversal.",60),
("DSA","CAT-02","Medium","Coding","Coding problem: Given an array of integers, return the first pair of indices whose values sum to target. Explain the data structure you use.","Use a hash map to store previously seen values and their indices; for each value x, check whether target-x is present.","","","Expected O(n) time and O(n) space; handle no solution explicitly.","Award marks for correct complement lookup, indices and complexity.",150),
("DSA","CAT-02","Medium","Explanation","Explain how a balanced binary search tree keeps search efficient, and compare its expected search time with an unbalanced tree.","A balanced BST keeps height O(log n), so search/insert/delete are O(log n); a skewed tree can reach O(n).","","","Mention rotations or balancing invariant and worst-case contrast.","Award marks for invariant and complexity.",120),
("DSA","CAT-02","Hard","MCQ","A directed acyclic graph has 8 vertices and 10 edges. Which algorithm can produce a valid topological ordering in O(V+E)?","Kahn's algorithm",["Dijkstra's algorithm","Kahn's algorithm","Kruskal's algorithm","Binary search"],"Kahn's algorithm repeatedly removes zero-indegree vertices.","","Recognize DAG ordering and complexity.",90),
# OOP (4): 2 Easy, 1 Medium, 1 Hard
("OOP","CAT-05","Easy","MCQ","In object oriented programming, which concept hides internal state and exposes controlled operations?","Encapsulation",["Inheritance","Encapsulation","Compilation","Recursion"],"Encapsulation bundles state and behavior behind a public interface.","","Identify core OOP concepts.",60),
("OOP","CAT-05","Easy","Explanation","Explain inheritance in object oriented programming and give a simple example.","A child class reuses or extends behavior from a parent class, e.g. class SavingsAccount(Account).","","","","Explain parent/child relationship and reuse.",90),
("OOP","CAT-05","Medium","Scenario-Based","Scenario: A payment service supports card, mobile wallet and bank transfer. New payment methods should be added without changing checkout logic. Which OOP design would you use and why?","Define a PaymentMethod interface/abstract class with pay(); implement CardPayment, WalletPayment and BankTransfer. Checkout depends on the abstraction (polymorphism / strategy).","","","Look for abstraction, polymorphism and open/closed design.","Score design choice, extensibility and reasoning.",120),
("OOP","CAT-05","Hard","MCQ","Which statement best describes the Liskov Substitution Principle?","A subtype should be usable wherever its base type is expected without breaking correctness.",["Every class must have one method","A subtype should be usable wherever its base type is expected without breaking correctness.","Subclasses must expose all fields publicly","Inheritance should replace composition in every case"],"LSP is about behavioral substitutability.","","Check understanding of SOLID and substitutability.",90),
# Networking (4): 2 Easy, 1 Medium, 1 Hard
("Networking","CAT-08","Easy","MCQ","Which transport protocol provides reliable, ordered byte delivery?","TCP",["UDP","TCP","ARP","ICMP"],"TCP provides connection-oriented reliable ordered delivery.","","Distinguish TCP from UDP.",60),
("Networking","CAT-08","Easy","MCQ","What is the primary purpose of DNS in computer networking?","Translate domain names to IP addresses",["Encrypt database rows","Translate domain names to IP addresses","Assign process IDs","Compress HTTP responses"],"DNS resolves names to records such as IP addresses.","","Know DNS resolution.",60),
("Networking","CAT-08","Medium","Scenario-Based","Scenario: Users can reach your service by IP address but not by hostname. Which networking component would you investigate first, and what checks would you perform?","Investigate DNS: verify resolver configuration, query the hostname with dig/nslookup, inspect A/AAAA records, TTL and authoritative nameserver health.","","","Troubleshooting should isolate name resolution before application code.","Score diagnosis and practical checks.",120),
("Networking","CAT-08","Hard","Explanation","Explain the TCP three-way handshake and why a service behind a firewall may accept SYN packets but never establish a connection.","Client sends SYN; server responds SYN-ACK; client returns ACK. A firewall, routing issue or dropped return packet can prevent completion.","","","Describe sequence and a plausible network failure.","Score protocol accuracy and diagnosis.",120),
# Coding (5): 2 Easy, 2 Medium, 1 Hard
("Coding","CAT-03","Easy","Coding","Coding problem: Write a function that returns the number of vowels in a string. State its time complexity.","def count_vowels(s):\n    return sum(ch.lower() in 'aeiou' for ch in s)","","","Expected O(n) time and O(1) extra space.","Score correctness, case handling and complexity.",90),
("Coding","CAT-03","Easy","MCQ","What is the time complexity of accessing an element by index in a Python list?","O(1)",["O(1)","O(log n)","O(n)","O(n log n)"],"Array-backed list indexing is constant time.","","Check basic coding complexity.",60),
("Coding","CAT-03","Medium","Coding","Coding problem: Given a string, return True if it is a palindrome after lowercasing and removing non-alphanumeric characters.","def is_palindrome(s):\n    t = ''.join(c.lower() for c in s if c.isalnum())\n    return t == t[::-1]","","","Normalize then compare from both ends or reverse.","Score normalization and comparison.",120),
("Coding","CAT-03","Medium","Scenario-Based","Scenario: An API endpoint becomes slow only when a customer has thousands of records. Describe how you would reproduce, profile and fix the issue.","Reproduce with representative data; inspect query count and timings; profile hot paths; add pagination/indexes or batch queries; add regression test and measure.","","","Should identify data-volume sensitivity and evidence-led optimization.","Score diagnosis, measurement and safe fix.",150),
("Coding","CAT-03","Hard","Coding","Coding problem: Given a list of intervals, merge all overlapping intervals and return the merged list. Describe complexity.","Sort intervals by start; scan once, extending the last merged interval when overlap exists. Complexity O(n log n) time and O(n) output space.","","","Expected sort + linear sweep; handle empty input.","Score algorithm, edge cases and complexity.",180),
# Database (5): 2 Easy, 1 Medium, 2 Hard
("Database","CAT-01","Easy","MCQ","In SQL, which clause filters individual rows before GROUP BY aggregation?","WHERE",["HAVING","WHERE","ORDER BY","DISTINCT"],"WHERE filters rows before grouping; HAVING filters groups.","","Basic SQL clause semantics.",60),
("Database","CAT-01","Easy","SQL","SQL task: Return the names of customers who live in Dhaka, sorted alphabetically.","SELECT name FROM customers WHERE city = 'Dhaka' ORDER BY name","","","Expected SQL uses WHERE and ORDER BY.","Run against the built-in customers table.",90),
("Database","CAT-01","Medium","Explanation","Explain the difference between an INNER JOIN and a LEFT JOIN, including what happens when there is no matching row.","INNER JOIN returns only matching pairs. LEFT JOIN preserves every left-side row and fills right-side columns with NULL when no match exists.","","","Use a concrete unmatched-row explanation.","Score correctness and example.",90),
("Database","CAT-01","Hard","SQL","SQL task: For each customer, return customer name and total completed order amount. Include customers with no completed orders as zero; sort highest total first then name.","SELECT c.name, COALESCE(SUM(CASE WHEN o.status = 'completed' THEN o.amount ELSE 0 END), 0) AS total FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY total DESC, c.name","","","Expected LEFT JOIN, conditional sum, COALESCE and deterministic ordering.","Compare query results on generated SQL datasets.",180),
("Database","CAT-01","Hard","SQL","SQL task: Return the second-highest distinct order amount among completed orders. Return NULL if it does not exist.","SELECT MAX(amount) FROM orders WHERE status = 'completed' AND amount < (SELECT MAX(amount) FROM orders WHERE status = 'completed')","","","Expected distinct second-highest value using MAX below the maximum.","Compare query results on generated SQL datasets.",150),
# Critical thinking (2): 1 Medium, 1 Hard
("Critical Thinking","CAT-06","Medium","Scenario-Based","Scenario: A dashboard shows sales 18% lower than yesterday, but the database ingestion job also failed overnight. What would you verify before reporting a business decline?","Check data freshness and ingestion status, compare source totals and time windows, inspect late-arriving records, validate filters, then communicate a data-quality caveat.","","","Do not assume business change until pipeline freshness is verified.","Score evidence checks and communication.",120),
("Critical Thinking","CAT-06","Hard","Explanation","A team can ship either a quick patch that hides an intermittent error or spend a day finding the root cause. Explain how you would decide under a high-impact production incident.","Assess user impact and risk, mitigate safely with rollback/feature flag if needed, preserve evidence, communicate, investigate root cause, then verify permanent fix and prevention.","","","Balance immediate mitigation with root-cause analysis and auditability.","Score prioritization, safety and reasoning.",150),
]

# Five questions per skill: 1 Easy, 2 Medium, 2 Hard.
CV_QUESTIONS = [
# Python
("Python","Easy","MCQ","What does the Python expression [x*x for x in range(3)] produce?","[0, 1, 4]",["[1, 4, 9]","[0, 1, 4]","[0, 1, 2]","An error"],"A list comprehension squares 0, 1 and 2.","","Basic Python comprehension.",60),
("Python","Medium","Coding","Coding problem (Python): Given a list of integers, return the first non-repeating value, or None if every value repeats.","from collections import Counter\ndef first_unique(values):\n    counts = Counter(values)\n    return next((v for v in values if counts[v] == 1), None)","","","Use frequency counting and preserve original order.","Score correctness and O(n) expected complexity.",120),
("Python","Medium","Scenario-Based","Scenario (Python): A pandas DataFrame contains duplicate customer rows and inconsistent whitespace in email addresses. Describe a safe cleaning sequence and how you would verify it.","Preserve raw input; normalize email with strip/lower; identify duplicate keys; inspect conflicts before dropping; deduplicate by documented rule; compare row counts and sample records.","","","Mention reproducibility and avoid blind duplicate deletion.","Score safe data cleaning and validation.",120),
("Python","Hard","Coding","Coding problem (Python): Implement a generator that yields batches of at most k items from an iterable without loading the entire iterable into memory.","def batches(items, k):\n    if k <= 0: raise ValueError('k must be positive')\n    batch = []\n    for item in items:\n        batch.append(item)\n        if len(batch) == k:\n            yield batch\n            batch = []\n    if batch: yield batch","","","Streaming generator; validate k and yield final partial batch.","Score laziness, boundaries and memory behavior.",180),
("Python","Hard","Explanation","A Python API has rising memory use under load. Explain how you would investigate whether the cause is retained references, unbounded caching or large DataFrame copies.","Reproduce and profile; inspect heap/object growth with tracemalloc; review cache bounds and global references; inspect DataFrame copy patterns; test streaming/chunking and confirm memory under load.","","","Evidence-led investigation with measurable verification.","Score diagnostic approach and safe remediation.",150),
# Git
("Git","Easy","MCQ","Which Git command creates a local commit from staged changes?","git commit",["git push","git commit","git fetch","git clone"],"git commit records staged changes in local history.","","Basic Git workflow.",60),
("Git","Medium","Scenario-Based","Scenario (Git): Two developers changed the same lines in a shared branch and a merge reports conflicts. Describe a safe resolution workflow.","Inspect status and conflict markers; understand both changes; edit intended result; run tests; git add resolved files; complete merge/rebase; review diff and push.","","","No blind acceptance of ours/theirs; test the merged result.","Score safe conflict resolution.",120),
("Git","Medium","Coding","Git command task: Show the commands to create a feature branch named feature/report, stage only report.py, and create a commit with message Add report export.","git switch -c feature/report\ngit add report.py\ngit commit -m \"Add report export\"","","","Commands should create the branch, stage only the requested file and commit it.","Score command order and scope.",90),
("Git","Hard","Scenario-Based","Scenario (Git): A secret API key was committed and pushed to a public repository. What steps do you take immediately and afterward?","Revoke/rotate the exposed key immediately; notify owners; audit usage; remove from current code and history if appropriate; force-push coordinated rewrite; invalidate caches/forks where possible; add secret scanning and prevention.","","","History rewrite alone does not make a leaked secret safe.","Score incident response and prevention.",150),
("Git","Hard","Explanation","Explain how you would use interactive rebase to clean up a feature branch before review, and identify when not to rewrite history.","Use rebase -i to squash/reword/reorder commits and resolve conflicts; run tests. Avoid rewriting shared/public history without coordination; prefer merge commits when history preservation is required.","","","Discuss shared branch safety.","Score workflow and collaboration.",120),
# AI & Machine Learning
("AI & Machine Learning","Easy","MCQ","In supervised machine learning, what does the training dataset contain?","Examples with input features and target labels",["Only unlabelled inputs","Examples with input features and target labels","Only model weights","Only evaluation metrics"],"Supervised learning uses labelled examples.","","Basic supervised-learning concept.",60),
("AI & Machine Learning","Medium","Scenario-Based","Scenario (AI/ML): A model has 99% training accuracy but 72% validation accuracy. Name likely causes and the first checks you would perform.","Suspect overfitting, leakage or distribution mismatch; inspect split strategy, duplicates/leakage, class balance, learning curves and regularization; compare validation data provenance.","","","Evidence-based diagnosis, not just tuning.","Score likely causes and validation plan.",120),
("AI & Machine Learning","Medium","Coding","Coding task (machine learning): Given true-positive, false-positive and false-negative counts, write a Python function returning precision and recall, using 0 when a denominator is zero.","def precision_recall(tp, fp, fn):\n    precision = tp / (tp + fp) if tp + fp else 0\n    recall = tp / (tp + fn) if tp + fn else 0\n    return precision, recall","","","Use correct denominators and guard zero division.","Score formulas and edge cases.",120),
("AI & Machine Learning","Hard","Scenario-Based","Scenario (AI/ML): Your production classifier's performance drops after a new customer segment launches. Explain how you would detect and respond to model drift.","Monitor feature/label distributions and delayed outcome metrics by segment; check data pipeline/schema changes; compare cohorts; alert, investigate, retrain only after validation and controlled rollout.","","","Distinguish data drift from concept drift and protect deployment.","Score monitoring, segmentation and controlled response.",150),
("AI & Machine Learning","Hard","Explanation","Explain how you would evaluate a retrieval-augmented generation system beyond answer fluency, including retrieval and groundedness.","Measure retrieval recall/precision and ranking; assess context relevance, answer faithfulness/groundedness, citation correctness, answer relevance, latency and cost; use curated test sets and human review.","","","Separate retrieval quality from generation quality.","Score metric coverage and evaluation discipline.",150),
# SQL
("SQL","Easy","MCQ","Which SQL aggregate counts all rows, including rows where a particular column is NULL?","COUNT(*)",["COUNT(column)","COUNT(*)","SUM(*)","TOTAL(column)"],"COUNT(*) counts rows; COUNT(column) ignores NULL values.","","Basic SQL aggregate behavior.",60),
("SQL","Medium","SQL","SQL task: Return each city and the number of customers in that city, ordered by city.","SELECT city, COUNT(*) AS customer_count FROM customers GROUP BY city ORDER BY city","","","Expected GROUP BY and COUNT.","Compare query results on generated SQL datasets.",120),
("SQL","Medium","Scenario-Based","Scenario (SQL): A report query became slow after the orders table grew tenfold. Describe how you would diagnose and improve it without guessing indexes.","Inspect query plan and timings; check predicates, join keys and cardinality; identify scans/sorts; test a targeted index; compare execution plans and write/read trade-offs.","","","Use EXPLAIN and measurement; avoid indiscriminate indexes.","Score diagnosis and validation.",120),
("SQL","Hard","SQL","SQL task: Return customers whose completed-order total exceeds 1000. Output customer name and total, highest total first.","SELECT c.name, SUM(o.amount) AS total FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name HAVING SUM(o.amount) > 1000 ORDER BY total DESC","","","Expected WHERE, GROUP BY, HAVING and ordering.","Compare query results on generated SQL datasets.",150),
("SQL","Hard","Explanation","Explain how a database transaction and an appropriate isolation level help prevent inconsistent updates when two users purchase the last item in stock.","Use an atomic conditional update or row lock within a transaction; choose isolation/locking to prevent lost updates or overselling; handle serialization/deadlock retries and commit/rollback.","","","Explain concurrency control and atomicity.","Score correctness and practical safeguards.",150),
]

def insert_question(qid, category, subcategory, difficulty, qtype, prompt, answer,
                    options, explanation, reference, rubric, seconds):
    run("""INSERT INTO question_bank(
        id,category_id,subcategory,question_type,difficulty_level,question_text,
        options,correct_answer,explanation,reference_answer,grading_rubric,marks,
        time_allocation,status,created_by,created_at,version
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO NOTHING""", (
        qid, category, subcategory, qtype, difficulty, prompt, json.dumps(options),
        answer, explanation, reference, rubric, 1, seconds, "active", ACTOR, NOW, 1
    ))

def seed():
    from main import init_db, run
    import enterprise
    init_db()
    # Ensure admin question-bank tables exist and preserve existing legacy sample data.
    enterprise._seed_question_bank()
    for index, row in enumerate(FUNDAMENTALS, 1):
        topic, category, difficulty, qtype, prompt, answer, options, explanation, reference, rubric, seconds = row
        insert_question(f"demo-gr-f-{index:02d}", category, topic, difficulty, qtype,
                        prompt, answer, options, explanation, reference, rubric, seconds)
    for index, row in enumerate(CV_QUESTIONS, 1):
        skill, difficulty, qtype, prompt, answer, options, explanation, reference, rubric, seconds = row
        insert_question(f"demo-gr-cv-{index:02d}", "CAT-10", skill, difficulty, qtype,
                        prompt, answer, options, explanation, reference, rubric, seconds)
    total = len(FUNDAMENTALS) + len(CV_QUESTIONS)
    print(f"[graphrag-seed] inserted/verified {total} deterministic synthetic questions")
    print("[graphrag-seed] Fundamental: 25 | CV: Python 5, Git 5, AI/ML 5, SQL 5")
    print("[graphrag-seed] Safe to rerun; existing question IDs are not overwritten.")

if __name__ == "__main__":
    seed()
