import json
import pymongo
import numpy as np
import matplotlib.pyplot as plt
from typing import List, Dict
import os
from docx import Document
from collections import defaultdict
import subprocess

class DSATWhatIfAnalyzer:
    """Analyzes DSAT performance and identifies high-impact questions for score improvement."""
    
    def __init__(self, scoring_maps: List[Dict] = None, adaptive_threshold: float = 0.6):
        self.scoring_maps = scoring_maps
        self.adaptive_threshold = adaptive_threshold
        self.placeholder_scoring = False
        if not scoring_maps:
            print("Warning: No scoring maps provided. Using placeholder scoring.")
            self.placeholder_scoring = True
            self.scoring_maps = [
                {"key": "Math", "map": [{"raw": i, "hard": 200 + i * 15, "easy": 200 + i * 10} for i in range(45)]},
                {"key": "Reading and Writing", "map": [{"raw": i, "hard": 200 + i * 11, "easy": 200 + i * 8} for i in range(55)]}
            ]

    def load_student_data(self, collection: pymongo.collection.Collection = None, source_file: str = None, use_mongodb: bool = True) -> List[Dict]:
        """Load student response data from MongoDB or local JSON file."""
        data = []
        if use_mongodb and collection is not None:
            try:
                data = list(collection.find({"source_file": source_file}))
                if not data:
                    print(f"No data found for {source_file} in student_results collection. Falling back to local JSON file.")
                    use_mongodb = False
            except pymongo.errors.PyMongoError as e:
                print(f"MongoDB error: {e}. Falling back to local JSON file.")
                use_mongodb = False
        if not use_mongodb or not data:
            if not os.path.exists(source_file):
                raise FileNotFoundError(f"File {source_file} not found.")
            with open(source_file, 'r') as f:
                data = json.load(f)
        if not data:
            raise ValueError(f"No valid data loaded from {source_file}.")
        sections = set(q.get('section', 'Unknown') for q in data)
        question_ids = [q.get('question_id', q.get('_id')) for q in data]
        if len(question_ids) != len(set(question_ids)):
            print(f"Warning: Duplicate question IDs detected in {source_file}. Using 'question_id' for uniqueness.")
        print(f"Loaded {source_file}. Unique sections found: {sections}")
        print(f"First few records: {data[:3]}")
        return data

    def determine_module2_difficulty(self, module1_correct: int, module1_total: int) -> str:
        """Determine Module 2 difficulty based on Module 1 performance."""
        if module1_total == 0:
            print("Warning: No Module 1 questions found. Defaulting to 'easy' Module 2.")
            return 'easy'
        return 'hard' if (module1_correct / module1_total) >= self.adaptive_threshold else 'easy'

    def calculate_scaled_score(self, subject: str, raw_score: int, module2_difficulty: str) -> int:
        """Calculate scaled score using scoring maps."""
        map_data = next((item['map'] for item in self.scoring_maps if item['key'] == subject), None)
        if not map_data:
            print(f"Warning: No scoring map for {subject}. Returning default score.")
            return 200
        for score in map_data:
            if score['raw'] == raw_score:
                return score[module2_difficulty]
        return 200

    def analyze_performance(self, student_data: List[Dict], source_file: str) -> Dict:
        """Analyze student performance by subject and module, including time analysis."""
        performance = {'Math': {'Module 1': {}, 'Module 2': {}}, 'Reading and Writing': {'Module 1': {}, 'Module 2': {}}}
        
        for subject in ['Math', 'Reading and Writing']:
            subject_data = [q for q in student_data if q.get('subject', {}).get('name') == subject]
            module1_data = [q for q in subject_data if q.get('section') == 'Static']
            module2_data = [q for q in subject_data if q.get('section') in ['Hard', 'Adaptive', 'Module 2', 'AdaptiveHard']]
            
            module1_correct = sum(1 for q in module1_data if q.get('correct'))
            module1_total = len(module1_data)
            module2_correct = sum(1 for q in module2_data if q.get('correct'))
            module2_total = len(module2_data)
            raw_score = module1_correct + module2_correct
            
            if module1_total == 0:
                print(f"Warning: No Module 1 questions found for {subject} in {source_file}. Skipping scoring.")
                performance[subject]['Module 1'] = {'correct': 0, 'total': 0, 'accuracy': 0}
                performance[subject]['Module 2'] = {'correct': 0, 'total': 0, 'difficulty': 'easy'}
                performance[subject]['raw_score'] = 0
                performance[subject]['scaled_score'] = 200
                performance[subject]['slow_questions'] = []
                performance[subject]['weak_topics'] = {}
                performance[subject]['topic_clusters'] = {}
                continue
            
            if module2_total == 0:
                print(f"Warning: No Module 2 questions found for {subject} in {source_file}. Scores based on Module 1 only.")
            
            module2_difficulty = self.determine_module2_difficulty(module1_correct, module1_total)
            scaled_score = self.calculate_scaled_score(subject, raw_score, module2_difficulty)
            
            slow_questions = [
                {'question_id': q.get('question_id', q.get('_id')), 'topic': q.get('topic', {}).get('name'), 
                 'time_spent_s': q.get('time_spent', 0) / 1000}
                for q in subject_data if q.get('time_spent', 0) / 1000 > 60
            ]
            
            topics = defaultdict(lambda: {'correct': 0, 'total': 0, 'time_spent_s': []})
            for q in subject_data:
                topic = q.get('topic', {}).get('name')
                if topic:
                    topics[topic]['total'] += 1
                    if q.get('correct'):
                        topics[topic]['correct'] += 1
                    topics[topic]['time_spent_s'].append(q.get('time_spent', 0) / 1000)
            
            performance[subject]['Module 1'] = {
                'correct': module1_correct,
                'total': module1_total,
                'accuracy': module1_correct / module1_total if module1_total > 0 else 0
            }
            performance[subject]['Module 2'] = {
                'correct': module2_correct,
                'total': module2_total,
                'difficulty': module2_difficulty
            }
            performance[subject]['raw_score'] = raw_score
            performance[subject]['scaled_score'] = scaled_score
            performance[subject]['slow_questions'] = slow_questions
            performance[subject]['weak_topics'] = {
                topic: f"{(stats['correct'] / stats['total'])*100:.2f}%"
                for topic, stats in topics.items() if stats['total'] > 0 and stats['correct'] / stats['total'] < 0.5
            }
            performance[subject]['topic_clusters'] = {
                topic: f"Acc: {(stats['correct'] / stats['total'])*100:.2f}%, Avg Time: {sum(stats['time_spent_s']) / len(stats['time_spent_s']):.2f}s"
                for topic, stats in topics.items() if stats['total'] > 0
            }
        
        return performance

    def what_if_analysis(self, student_data: List[Dict], source_file: str, additional_correct: int = 2) -> Dict:
        """Perform what-if analysis by simulating additional correct Module 1 answers."""
        results = {}
        for subject in ['Math', 'Reading and Writing']:
            subject_data = [q for q in student_data if q.get('subject', {}).get('name') == subject]
            module1_data = [q for q in subject_data if q.get('section') == 'Static']
            module2_data = [q for q in subject_data if q.get('section') in ['Hard', 'Adaptive', 'Module 2', 'AdaptiveHard']]
            
            module1_correct = sum(1 for q in module1_data if q.get('correct'))
            module1_total = len(module1_data)
            module2_correct = sum(1 for q in module2_data if q.get('correct'))
            current_raw_score = module1_correct + module2_correct
            current_difficulty = self.determine_module2_difficulty(module1_correct, module1_total)
            current_score = self.calculate_scaled_score(subject, current_raw_score, current_difficulty)
            
            new_module1_correct = min(module1_correct + additional_correct, module1_total)
            new_difficulty = self.determine_module2_difficulty(new_module1_correct, module1_total)
            new_raw_score = new_module1_correct + module2_correct
            new_score = self.calculate_scaled_score(subject, new_raw_score, new_difficulty)
            
            high_impact_questions = [
                q for q in module1_data 
                if not q.get('correct')
            ]
            
            results[subject] = {
                'current_score': current_score,
                'new_score': new_score,
                'score_gain': new_score - current_score,
                'high_impact_questions': [
                    {'question_id': q.get('question_id', q.get('_id')), 'topic': q.get('topic', {}).get('name'), 
                     'complexity': q.get('compleixty', q.get('complexity', 'Unknown'))}
                    for q in high_impact_questions[:additional_correct]
                ]
            }
        
        return results

    def calculate_prediction_accuracy(self, data: List[Dict], threshold: float) -> float:
        """Calculate accuracy of threshold in predicting Module 2 difficulty."""
        correct_predictions = 0
        total_valid = 0
        for student in data:
            if student['module1_total'] == 0:
                continue
            predicted = 'hard' if (student['module1_correct'] / student['module1_total']) >= threshold else 'easy'
            actual = 'hard' if student['got_hard_module2'] else 'easy'
            if predicted == actual:
                correct_predictions += 1
            total_valid += 1
        return correct_predictions / total_valid if total_valid else 0

    def find_optimal_threshold(self, data: List[Dict]) -> float:
        """Find optimal Module 1 threshold for Module 2 difficulty."""
        best_accuracy = 0
        best_threshold = 0.5
        for threshold in np.arange(0.3, 0.8, 0.05):
            accuracy = self.calculate_prediction_accuracy(data, threshold)
            if accuracy > best_accuracy:
                best_accuracy = accuracy
                best_threshold = threshold
        return best_threshold

use_mongodb = True
try:
    client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)
    client.server_info() 
    db = client["dsat_analysis"]
    scoring_collection = db["sat_scoring"]
    student_collection = db["student_results"]
    print("Connected to MongoDB at mongodb://localhost:27017/")
except pymongo.errors.ServerSelectionTimeoutError as e:
    print(f"MongoDB connection failed: {e}. Ensure MongoDB server is running on localhost:27017.")
    try:
        subprocess.run([r"C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe", "--dbpath", r"C:\data\db"], check=True)
        client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)
        client.server_info()
        db = client["dsat_analysis"]
        scoring_collection = db["sat_scoring"]
        student_collection = db["student_results"]
        print("Started MongoDB and connected successfully.")
    except (subprocess.CalledProcessError, pymongo.errors.PyMongoError) as e2:
        print(f"Failed to start MongoDB: {e2}. Falling back to local JSON files.")
        use_mongodb = False
except pymongo.errors.PyMongoError as e:
    print(f"MongoDB error: {e}. Falling back to local JSON files.")
    use_mongodb = False

required_files = [
    'scoring_DSAT_v2.json',
    '66fece285a916f0bb5aea9c5user_attempt_v3.json',
    '67f2aae2c084263d16dbe462user_attempt_v2.json',
    'What-if-analysis.docx'
]
missing_files = [f for f in required_files if not os.path.exists(f)]
if missing_files:
    print(f"Warning: Missing files: {missing_files}")

scoring_file = 'scoring_DSAT_v2.json'
scoring_maps = None
if os.path.exists(scoring_file):
    with open(scoring_file, 'r') as f:
        scoring_maps = json.load(f)
    if use_mongodb:
        try:
            scoring_collection.drop()
            scoring_collection.insert_many(scoring_maps)
            print(f"Imported {scoring_file} into sat_scoring collection")
        except pymongo.errors.PyMongoError as e:
            print(f"Failed to import {scoring_file} to MongoDB: {e}. Using local scoring maps.")
else:
    print(f"Warning: {scoring_file} not found. Using placeholder scoring maps.")

analyzer = DSATWhatIfAnalyzer(scoring_maps, adaptive_threshold=0.6)

file_paths = [
    '67f2aae2c084263d16dbe462user_attempt_v2.json',
    '66fece285a916f0bb5aea9c5user_attempt_v3.json'
]

if use_mongodb:
    for file_path in file_paths:
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    student_data = json.load(f)
                    for record in student_data:
                        record['source_file'] = file_path
                        if 'question_id' in record:
                            record['_id'] = f"{record['question_id']}_{file_path}"
                        else:
                            record['_id'] = f"{record['_id']}_{file_path}"
                student_collection.drop()  
                try:
                    student_collection.insert_many(student_data, ordered=False)
                    print(f"Imported {file_path} into student_results collection")
                except pymongo.errors.BulkWriteError as bwe:
                    print(f"Duplicate key errors in {file_path}. Inserting records individually.")
                    inserted_ids = set()
                    for record in student_data:
                        try:
                            if record['_id'] not in inserted_ids:
                                student_collection.insert_one(record)
                                inserted_ids.add(record['_id'])
                        except pymongo.errors.DuplicateKeyError:
                            print(f"Skipping duplicate _id: {record['_id']}")
            except pymongo.errors.PyMongoError as e:
                print(f"Failed to import {file_path} to MongoDB: {e}. Processing locally.")
        else:
            print(f"Warning: {file_path} not found. Skipping import.")
else:
    print("MongoDB unavailable. Processing JSON files locally.")

docx_file = 'What-if-analysis.docx'
if os.path.exists(docx_file):
    print(f"\nProcessing {docx_file}")
    doc = Document(docx_file)
    print("Extracted text from What-if-analysis.docx (first 5 paragraphs):")
    for para in doc.paragraphs[:5]:
        if para.text.strip():
            print(para.text)
else:
    print("Warning: What-if-analysis.docx not found. Using embedded logic.")

results = []
for file_path in file_paths:
    print(f"\nAnalyzing student: {file_path}")
    try:
        student_data = analyzer.load_student_data(student_collection if use_mongodb else None, file_path, use_mongodb)
        
        performance = analyzer.analyze_performance(student_data, file_path)
        for subject, metrics in performance.items():
            if metrics['Module 1']['total'] == 0 and metrics['Module 2']['total'] == 0:
                print(f"Skipping {subject}: No questions found.")
                continue
            print(f"{subject}:")
            print(f"  Module 1: {metrics['Module 1']['correct']}/{metrics['Module 1']['total']} "
                  f"({metrics['Module 1']['accuracy']*100:.2f}%)")
            print(f"  Module 2: {metrics['Module 2']['correct']}/{metrics['Module 2']['total']} "
                  f"({metrics['Module 2']['difficulty']})")
            print(f"  Scaled Score: {metrics['scaled_score']} {'(placeholder)' if analyzer.placeholder_scoring else ''}")
            print("  Weak Topics:", metrics['weak_topics'])
            print("  Slow Questions (>60s):", metrics['slow_questions'])
            print("  Topic Clusters:", metrics['topic_clusters'])
            results.append({
                'file': file_path,
                'subject': subject,
                'scaled_score': metrics['scaled_score']
            })
        
        what_if_results = analyzer.what_if_analysis(student_data, file_path, additional_correct=2)
        print("\nWhat-If Analysis (Correct 2 Additional Module 1 Questions):")
        for subject, result in what_if_results.items():
            if performance[subject]['Module 1']['total'] == 0:
                print(f"Skipping {subject}: No Module 1 questions for what-if analysis.")
                continue
            print(f"{subject}:")
            print(f"  Current Score: {result['current_score']} {'(placeholder)' if analyzer.placeholder_scoring else ''}")
            print(f"  New Score: {result['new_score']} {'(placeholder)' if analyzer.placeholder_scoring else ''}")
            print(f"  Score Gain: {result['score_gain']}")
            print("  High-Impact Questions:", result['high_impact_questions'])
            results.append({
                'file': file_path,
                'subject': f"{subject} (+2)",
                'scaled_score': result['new_score']
            })
    
    except Exception as e:
        print(f"Error processing {file_path}: {e}")

threshold_data = [
    {"module1_correct": 20, "module1_total": 22, "got_hard_module2": True},
    {"module1_correct": 25, "module1_total": 27, "got_hard_module2": True}
]
optimal_threshold = analyzer.find_optimal_threshold(threshold_data)
print(f"\nOptimal Threshold: {optimal_threshold*100:.2f}%")

if results:
    labels = [f"{r['subject']} ({r['file'].split('/')[-1]})" for r in results]
    scores = [r['scaled_score'] for r in results]
    plt.figure(figsize=(12, 6))
    plt.bar(labels, scores, color=['#4CAF50' if '+2' not in label else '#66BB6A' for label in labels])
    plt.xlabel('Subject and File')
    plt.ylabel('Scaled Score (200-800)')
    plt.title('What-If Analysis: Current vs. +2 Correct in Module 1')
    plt.xticks(rotation=45, ha='right')
    plt.ylim(0, 800)
    plt.tight_layout()
    plt.savefig('what_if_analysis.png')
    plt.close()
    print("Matplotlib chart saved to what_if_analysis.png")
else:
    print("No results to visualize. Please ensure student data files contain valid questions.")

chartjs_config = {
    "type": "bar",
    "data": {
        "labels": labels if results else [],
        "datasets": [{
            "label": "Scaled Score",
            "data": scores if results else [],
            "backgroundColor": ['#4CAF50' if '+2' not in label else '#66BB6A' for label in labels] if results else []
        }]
    },
    "options": {
        "scales": {
            "y": {
                "beginAtZero": True,
                "max": 800,
                "title": {"display": True, "text": "Scaled Score (200-800)"}
            },
            "x": {
                "title": {"display": True, "text": "Subject and File"}
            }
        },
        "plugins": {
            "title": {
                "display": True,
                "text": "What-If Analysis: Current vs. +2 Correct in Module 1"
            }
        }
    }
}
with open('chartjs_config.json', 'w') as f:
    json.dump(chartjs_config, f)
print("Chart.js config saved to chartjs_config.json")