"""Static R0 content: taxonomy, job profile, question pool, learning-resource index.
Everything here is curated by hand for the prototype and labelled MOCK/CURATED in the UI."""
import random

TAXONOMY = {
    "sql": {"name": "SQL", "aliases": ["sql", "mysql", "postgresql", "postgres", "sqlite", "t-sql", "joins", "group by"]},
    "python": {"name": "Python", "aliases": ["python", "pandas", "numpy", "jupyter", "scikit-learn"]},
    "statistics": {"name": "Statistics", "aliases": ["statistics", "statistical", "hypothesis testing", "regression", "probability", "a/b testing", "p-value"]},
    "visualization": {"name": "Data Visualization", "aliases": ["data visualization", "data visualisation", "tableau", "power bi", "matplotlib", "seaborn", "dashboard", "dashboards", "looker", "plotly"]},
    "excel": {"name": "Excel", "aliases": ["excel", "spreadsheet", "spreadsheets", "pivot table", "vlookup", "google sheets"]},
    "git": {"name": "Git", "aliases": ["git", "github", "gitlab"]},
    "communication": {"name": "Communication", "aliases": ["communication", "presentation", "presentations", "report writing", "stakeholder"]},
    "ai_ml": {"name": "AI & Machine Learning", "aliases": ["artificial intelligence", "machine learning", "deep learning", "tensorflow", "pytorch", "scikit-learn", "sklearn"]},
    "javascript": {"name": "JavaScript & TypeScript", "aliases": ["javascript", "typescript", "react", "node.js", "nodejs", "next.js"]},
    "java": {"name": "Java", "aliases": ["java", "spring boot"]},
    "docker": {"name": "Docker", "aliases": ["docker", "docker compose"]},
    "kubernetes": {"name": "Kubernetes", "aliases": ["kubernetes", "k8s"]},
    "aws": {"name": "AWS", "aliases": ["aws", "amazon web services"]},
    "azure": {"name": "Microsoft Azure", "aliases": ["azure"]},
    "gcp": {"name": "Google Cloud", "aliases": ["gcp", "google cloud"]},
    "mongodb": {"name": "MongoDB", "aliases": ["mongodb", "mongo"]},
    "neo4j": {"name": "Neo4j", "aliases": ["neo4j", "cypher"]},
    "meilisearch": {"name": "Meilisearch", "aliases": ["meilisearch"]},
    "linux": {"name": "Linux & Bash", "aliases": ["linux", "bash", "shell scripting"]},
    "networking": {"name": "Computer Networking", "aliases": ["computer networks", "networking", "tcp/ip", "dns", "osi model"]},
    "dsa": {"name": "Data Structures & Algorithms", "aliases": ["data structures", "algorithms", "big o", "dynamic programming"]},
    "oop": {"name": "Object-Oriented Programming", "aliases": ["object oriented programming", "oop", "polymorphism", "encapsulation", "inheritance"]},
    "cpp": {"name": "C++", "aliases": ["c++", "cpp"]},
    "csharp": {"name": "C#", "aliases": ["c#", "c sharp", ".net"]},
    "redis": {"name": "Redis", "aliases": ["redis"]},
    "html_css": {"name": "HTML & CSS", "aliases": ["html", "css", "tailwind", "bootstrap"]},
}
TESTABLE = ["sql", "python", "statistics", "visualization"]

# MOCK / CURATED job profile. Thresholds are SUGGESTIONS: recruiters own thresholds; nobody is blocked.
JOB = {
    "id": "jda-mock-001",
    "title": "Junior Data Analyst",
    "company": "Sample Employer (MOCK, curated for prototype)",
    "mock": True,
    "required": [
        {"skill": "sql", "threshold": 60},
        {"skill": "python", "threshold": 50},
        {"skill": "statistics", "threshold": 50},
        {"skill": "visualization", "threshold": 50},
    ],
    "preferred": ["excel", "git", "communication"],
}


def _mcq(qid, topic, diff, prompt, correct, wrongs, expl):
    opts = [correct] + list(wrongs)
    random.Random(qid).shuffle(opts)  # fixed per question; a real system shuffles per attempt
    return {"id": qid, "topic": topic, "difficulty": diff, "type": "mcq", "prompt": prompt,
            "options": opts, "answer": opts.index(correct), "explanation": expl}


def _sql(qid, diff, prompt, ref):
    return {"id": qid, "topic": "sql", "difficulty": diff, "type": "sql", "prompt": prompt, "ref": ref}


SQL_SCHEMA = ("customers(id INTEGER, name TEXT, city TEXT)\n"
              "orders(id INTEGER, customer_id INTEGER, amount INTEGER, status TEXT)  -- status: completed | cancelled | pending")

QUESTIONS = [
    # --- SQL concepts
    _mcq("sql-m1", "sql", 1, "Which clause filters rows BEFORE they are grouped?", "WHERE", ["HAVING", "ORDER BY", "LIMIT"], "WHERE filters rows; HAVING filters groups after aggregation."),
    _mcq("sql-m2", "sql", 1, "Which query returns the number of rows in table t?", "SELECT COUNT(*) FROM t", ["SELECT SUM(*) FROM t", "SELECT ROWS(t)", "SELECT LENGTH(t)"], "COUNT(*) counts rows."),
    _mcq("sql-m3", "sql", 2, "What does a LEFT JOIN return?", "All rows from the left table, plus matching right rows (NULL when none)", ["Only rows that match in both tables", "All rows from the right table only", "Every combination of rows (Cartesian product)"], "LEFT JOIN keeps unmatched left rows."),
    _mcq("sql-m4", "sql", 2, "Which clause filters groups AFTER aggregation?", "HAVING", ["WHERE", "DISTINCT", "GROUP BY"], "HAVING applies to aggregated groups."),
    _mcq("sql-m5", "sql", 3, "Column x is NULL in 4 of 10 rows. What does SELECT COUNT(x) return?", "6", ["10", "4", "0"], "COUNT(column) ignores NULLs."),
    _mcq("sql-m6", "sql", 3, "Which query returns the second-highest salary?", "SELECT MAX(salary) FROM emp WHERE salary < (SELECT MAX(salary) FROM emp)", ["SELECT MIN(salary) FROM emp", "SELECT salary FROM emp ORDER BY salary LIMIT 2", "SELECT MAX(salary) - 1 FROM emp"], "Exclude the maximum, then take the maximum of the rest."),
    # --- SQL hands-on (graded by comparing result sets on two generated datasets)
    _sql("sql-p1", 1, "How many customers live in Dhaka? Return one value.", "SELECT COUNT(*) FROM customers WHERE city = 'Dhaka'"),
    _sql("sql-p2", 1, "List the names of customers from Chattogram, alphabetically.", "SELECT name FROM customers WHERE city = 'Chattogram' ORDER BY name"),
    _sql("sql-p3", 2, "What is the total amount of all completed orders? Return one value.", "SELECT SUM(amount) FROM orders WHERE status = 'completed'"),
    _sql("sql-p4", 2, "For each city, count the orders placed by its customers. Columns: city, order_count. Order by city.", "SELECT c.city, COUNT(o.id) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.city ORDER BY c.city"),
    _sql("sql-p5", 3, "List names of customers who have never placed any order, alphabetically.", "SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.id IS NULL ORDER BY c.name"),
    _sql("sql-p6", 3, "Top 3 customers by total COMPLETED order amount. Columns: name, total. Highest first; break ties by name.", "SELECT c.name, SUM(o.amount) AS t FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id ORDER BY t DESC, c.name LIMIT 3"),
    # --- Python / pandas
    _mcq("py-m1", "python", 1, "What does len([4, 5, 6]) return?", "3", ["2", "6", "An error"], "len counts items."),
    _mcq("py-m2", "python", 1, "Which expression creates a dictionary?", "{'a': 1}", ["['a', 1]", "('a', 1)", "{'a'}"], "Curly braces with key: value pairs."),
    _mcq("py-m3", "python", 2, "In pandas, which call shows the first 5 rows of DataFrame df?", "df.head()", ["df.first()", "df.top()", "df.start()"], "head() defaults to 5 rows."),
    _mcq("py-m4", "python", 2, "What does df.groupby('city')['amount'].sum() return?", "A Series with the total amount per city", ["A single number", "The number of rows per city", "A sorted Python list"], "Grouped sum per key."),
    _mcq("py-m5", "python", 3, "Which replaces missing values in df['x'] with 0?", "df['x'].fillna(0)", ["df['x'].dropna(0)", "df['x'].isna(0)", "df['x'].missing(0)"], "fillna fills NaN."),
    _mcq("py-m6", "python", 3, "What does [x*x for x in range(4) if x % 2 == 0] produce?", "[0, 4]", ["[0, 1, 4, 9]", "[0, 2]", "[4]"], "Even x in 0..3 are 0 and 2; squares are 0 and 4."),
    # --- Statistics
    _mcq("st-m1", "statistics", 1, "What is the mean of 2, 4, 6?", "4", ["3", "6", "12"], "(2+4+6)/3 = 4."),
    _mcq("st-m2", "statistics", 1, "What is the median of 1, 3, 5, 7, 100?", "5", ["23.2", "7", "3"], "Middle value of the sorted list."),
    _mcq("st-m3", "statistics", 2, "Which measure is most affected by outliers?", "Mean", ["Median", "Mode", "Interquartile range"], "The mean uses every value's magnitude."),
    _mcq("st-m4", "statistics", 2, "A test gives p = 0.03 with alpha = 0.05. The correct reading is:", "Reject the null hypothesis at the 5% level", ["The null hypothesis is 3% likely", "The effect is large", "Accept the null hypothesis"], "p < alpha means reject H0; p is not the probability H0 is true."),
    _mcq("st-m5", "statistics", 3, "Ice-cream sales and drownings correlate at 0.9. Best interpretation?", "They are associated; a confounder such as hot weather may explain it", ["Ice cream causes drowning", "Drowning causes ice-cream sales", "There is no relationship"], "Correlation is not causation."),
    _mcq("st-m6", "statistics", 3, "A 95% confidence interval for a mean is best described as:", "A range built by a method that covers the true mean in about 95% of repeated samples", ["The range containing 95% of all individual observations", "The range where the sample mean falls 95% of the time", "A range with 5% error in every data point"], "Confidence refers to the procedure over repeated sampling."),
    # --- Visualization
    _mcq("vz-m1", "visualization", 1, "Best chart for a trend over time?", "Line chart", ["Pie chart", "Scatter plot of unrelated fields", "Treemap"], "Lines show change across ordered time."),
    _mcq("vz-m2", "visualization", 1, "Best chart for comparing values across categories?", "Bar chart", ["Line chart", "Gauge", "Radar chart with 20 axes"], "Bars compare lengths easily."),
    _mcq("vz-m3", "visualization", 2, "Which chart shows the distribution of one numeric variable?", "Histogram", ["Pie chart", "Stacked bar", "Treemap"], "Histograms bin numeric values."),
    _mcq("vz-m4", "visualization", 2, "Best chart for the relationship between two numeric variables?", "Scatter plot", ["Pie chart", "Donut chart", "Gauge"], "Scatter plots show correlation and clusters."),
    _mcq("vz-m5", "visualization", 3, "A bar chart's y-axis starts at 95 instead of 0, so small gaps look huge. This is:", "A truncated axis that exaggerates differences", ["Good practice for clarity", "Normalisation", "A log scale"], "Bar lengths should start at zero."),
    _mcq("vz-m6", "visualization", 3, "A manager dashboard has a 12-slice, 12-colour pie chart. Best improvement?", "Replace with a sorted bar chart and group small categories as 'Other'", ["Add more colours", "Make it 3D", "Remove the labels"], "Sorted bars are easier to read than many slices."),
]
QBYID = {q["id"]: q for q in QUESTIONS}

# Curated learning-resource index (R0: topic/level retrieval; pgvector RAG is on the roadmap).
# Licence field is conservative: a human curator must verify every entry before launch.
RESOURCES = [
    {"id": "r-sqlbolt", "topic": "sql", "level": 1, "title": "SQLBolt: Learn SQL with interactive lessons", "url": "https://sqlbolt.com/", "source": "SQLBolt", "licence": "Free to use (curator to verify)", "lang": "en", "minutes": 120},
    {"id": "r-khan-sql", "topic": "sql", "level": 1, "title": "Khan Academy: Intro to SQL", "url": "https://www.khanacademy.org/computing/computer-programming/sql", "source": "Khan Academy", "licence": "CC BY-NC-SA", "lang": "en", "minutes": 150},
    {"id": "r-mode", "topic": "sql", "level": 2, "title": "Mode SQL Tutorial (basic to advanced)", "url": "https://mode.com/sql-tutorial/", "source": "Mode Analytics", "licence": "Free to read (curator to verify)", "lang": "en", "minutes": 180},
    {"id": "r-kaggle-sql", "topic": "sql", "level": 2, "title": "Kaggle Learn: Intro to SQL", "url": "https://www.kaggle.com/learn/intro-to-sql", "source": "Kaggle", "licence": "Free (see Kaggle terms)", "lang": "en", "minutes": 180},
    {"id": "r-pytut", "topic": "python", "level": 1, "title": "The Python Tutorial (official)", "url": "https://docs.python.org/3/tutorial/", "source": "Python Software Foundation", "licence": "PSF licence", "lang": "en", "minutes": 240},
    {"id": "r-kaggle-py", "topic": "python", "level": 1, "title": "Kaggle Learn: Python", "url": "https://www.kaggle.com/learn/python", "source": "Kaggle", "licence": "Free (see Kaggle terms)", "lang": "en", "minutes": 300},
    {"id": "r-kaggle-pandas", "topic": "python", "level": 2, "title": "Kaggle Learn: Pandas", "url": "https://www.kaggle.com/learn/pandas", "source": "Kaggle", "licence": "Free (see Kaggle terms)", "lang": "en", "minutes": 240},
    {"id": "r-khan-stats", "topic": "statistics", "level": 1, "title": "Khan Academy: Statistics and probability", "url": "https://www.khanacademy.org/math/statistics-probability", "source": "Khan Academy", "licence": "CC BY-NC-SA", "lang": "en", "minutes": 360},
    {"id": "r-openintro", "topic": "statistics", "level": 2, "title": "OpenIntro Statistics (free textbook)", "url": "https://www.openintro.org/book/os/", "source": "OpenIntro", "licence": "CC BY-SA (verify edition)", "lang": "en", "minutes": 480},
    {"id": "r-kaggle-viz", "topic": "visualization", "level": 1, "title": "Kaggle Learn: Data Visualization", "url": "https://www.kaggle.com/learn/data-visualization", "source": "Kaggle", "licence": "Free (see Kaggle terms)", "lang": "en", "minutes": 240},
    {"id": "r-dataviz", "topic": "visualization", "level": 2, "title": "From Data to Viz: choose the right chart", "url": "https://www.data-to-viz.com/", "source": "data-to-viz.com", "licence": "Free to read (curator to verify)", "lang": "en", "minutes": 60},
]

NAMES = ["Rafi Ahmed", "Tanvir Hasan", "Maria Akter", "Salma Khatun", "Nusrat Jahan", "Imran Hossain", "Farhana Islam", "Sabbir Rahman",
         "Mehedi Hasan", "Anika Tabassum", "Rakib Chowdhury", "Tasnim Sultana", "Arif Mahmud", "Sadia Afrin", "Jahid Karim", "Lamia Noor",
         "Shihab Uddin", "Mim Sarker", "Hasib Alam", "Ruma Begum", "Zayed Khan", "Priya Das", "Nabil Ferdous", "Orpita Roy"]
CITIES = ["Dhaka", "Chattogram", "Sylhet", "Khulna", "Rajshahi"]
