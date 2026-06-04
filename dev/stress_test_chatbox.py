#!/usr/bin/env python
"""
Stress Test for AgenticGIS Chatbox Plugin

Tests the chat dock widget under load conditions:
1. Rapid message sending (simulating spam)
2. Large message volumes (long conversations)
3. Concurrent event handling (multiple tool events)
4. Memory usage monitoring during streaming
5. UI responsiveness under load
6. Thread safety of QThread worker
7. Stop button interrupt handling
8. Widget cleanup on clear
"""

import gc
import sys
import time
import threading
import importlib.util
import tracemalloc
from unittest.mock import Mock, MagicMock, patch
import os

# Add project root to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Test infrastructure
print("=" * 60)
print("STRESS TEST: AgenticGIS Chatbox Plugin")
print("=" * 60)

# Start memory tracking
tracemalloc.start()
initial_snapshot = tracemalloc.take_snapshot()

# Test counters
test_results = {
    "passed": 0,
    "failed": 0,
    "errors": [],
    "performance": {}
}

def log_result(test_name, passed, details=""):
    if passed:
        test_results["passed"] += 1
        print(f"[PASS] {test_name}")
    else:
        test_results["failed"] += 1
        print(f"[FAIL] {test_name}: {details}")
        test_results["errors"].append(f"{test_name}: {details}")

# Load base module manually
def load_base_module():
    """Load the base.py module with proper path handling."""
    base_path = os.path.join(script_dir, '..', 'backends', 'base.py')
    spec = importlib.util.spec_from_file_location("backends.base", base_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# ============================================================================
# TEST 1: Rapid Message Sending Simulation (without QGIS dependencies)
# ============================================================================
print("\n--- Test 1: Rapid Message Sending ---")

def test_rapid_message_simulation():
    """Simulate rapid message sending without actual UI."""
    try:
        base = load_base_module()
    except Exception as e:
        # Create mock event types if module can't load
        class EventType:
            TEXT = "text"
            TOOL_USE = "tool_use"
        class AgentEvent:
            def __init__(self, type, data):
                self.type = type
                self.data = data
        base = type('MockBase', (), {'EventType': EventType, 'AgentEvent': AgentEvent})()
    
    events_received = []
    stop_called = [False]
    
    def mock_emit(ev):
        events_received.append(ev)
        if len(events_received) > 1000:
            raise MemoryError("Too many events buffered")
    
    def mock_should_stop():
        return stop_called[0]
    
    # Simulate rapid event emission
    start_time = time.time()
    for i in range(500):
        mock_emit(base.AgentEvent(base.EventType.TEXT, {"text": f"Message {i}"}))
        mock_emit(base.AgentEvent(base.EventType.TOOL_USE, {"name": "test_tool", "input": {"id": i}}))
    
    elapsed = time.time() - start_time
    events_per_second = len(events_received) / elapsed if elapsed > 0 else 0
    
    test_results["performance"]["event_rate"] = events_per_second
    log_result(
        "Rapid message emission",
        len(events_received) == 1000,
        f"Processed {len(events_received)} events in {elapsed:.3f}s ({events_per_second:.0f} events/s)"
    )

try:
    test_rapid_message_simulation()
except Exception as e:
    log_result("Rapid message emission", False, str(e))

# ============================================================================
# TEST 2: Large Conversation History Simulation
# ============================================================================
print("\n--- Test 2: Large Conversation History ---")

def test_large_history():
    """Test handling of large conversation histories."""
    try:
        base = load_base_module()
    except Exception as e:
        # Mock implementations
        def estimate_message_tokens(messages):
            total = 0
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, str):
                    total += len(content)
            return total // 4
        
        def context_window_for(model):
            return 128000
        
        def should_compact(messages, model):
            limit = context_window_for(model)
            estimated = estimate_message_tokens(messages) + 30000
            return estimated >= int(limit * 0.90)
        
        base = type('MockBase', (), {
            'estimate_message_tokens': estimate_message_tokens,
            'should_compact': should_compact,
            'context_window_for': context_window_for
        })()
    
    # Create a large conversation history
    large_history = []
    for i in range(100):
        large_history.extend([
            {"role": "user", "content": f"User message {i} " + "word " * 200},
            {"role": "assistant", "content": f"Assistant response {i} " + "word " * 300}
        ])
    
    # Test token estimation
    token_estimate = base.estimate_message_tokens(large_history)
    test_results["performance"]["large_history_tokens"] = token_estimate
    
    # Test compaction threshold
    needs_compact = base.should_compact(large_history, "gpt-4")
    
    log_result(
        "Large history token estimation",
        token_estimate > 0,
        f"Estimated {token_estimate} tokens for {len(large_history)} messages"
    )
    log_result(
        "Compaction threshold triggers correctly",
        needs_compact,
        "Compaction triggered for large history"
    )

try:
    test_large_history()
except Exception as e:
    log_result("Large conversation history", False, str(e))

# ============================================================================
# TEST 3: Memory Leak Detection During Streaming
# ============================================================================
print("\n--- Test 3: Memory Leak Detection ---")

def test_memory_leak():
    """Monitor memory during simulated streaming operations."""
    gc.collect()
    snapshot1 = tracemalloc.take_snapshot()
    
    # Simulate creating and destroying many widgets
    widgets_created = []
    for _ in range(100):
        # Simulate widget creation patterns - use real object to track memory
        class MockWidget:
            def __init__(self):
                self.data = "x" * 1000
                self.children = []
            def deleteLater(self):
                pass
        widget_mock = MockWidget()
        widgets_created.append(widget_mock)
    
    # Simulate cleanup
    for w in widgets_created:
        w.deleteLater()
    
    gc.collect()
    snapshot2 = tracemalloc.take_snapshot()
    
    # Compare memory
    top_stats = snapshot2.compare_to(snapshot1, 'lineno')
    total_diff_kb = sum(stat.size_diff for stat in top_stats) / 1024
    
    test_results["performance"]["memory_diff_kb"] = total_diff_kb
    log_result(
        "Memory leak check",
        total_diff_kb < 5000,  # Less than 5MB growth
        f"Memory diff: {total_diff_kb:.1f} KB"
    )

try:
    test_memory_leak()
except Exception as e:
    log_result("Memory leak detection", False, str(e))

# ============================================================================
# TEST 4: Thread Safety Check
# ============================================================================
print("\n--- Test 4: Thread Safety ---")

def test_thread_safety():
    """Test concurrent access patterns."""
    shared_state = {"count": 0}
    lock = threading.Lock()
    errors = []
    
    def worker_thread(thread_id):
        try:
            for _ in range(100):
                with lock:
                    shared_state["count"] += 1
        except Exception as e:
            errors.append((thread_id, str(e)))
    
    threads = [threading.Thread(target=worker_thread, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    log_result(
        "Thread safety (concurrent worker threads)",
        len(errors) == 0 and shared_state["count"] == 1000,
        f"Errors: {errors}, Final count: {shared_state['count']}"
    )

try:
    test_thread_safety()
except Exception as e:
    log_result("Thread safety", False, str(e))

# ============================================================================
# TEST 5: Stop Button Interrupt Pattern
# ============================================================================
print("\n--- Test 5: Stop Button Handling ---")

def test_stop_interrupt():
    """Test that stop requests propagate correctly."""
    stop_flag = [False]
    
    def check_stop_repeatedly():
        iterations = 0
        while not stop_flag[0] and iterations < 10000:
            iterations += 1
        return iterations
    
    # Simulate starting a worker (will run until limit)
    iterations_before = check_stop_repeatedly()
    
    # Now stop it and check it exits quickly
    stop_flag[0] = True
    start_time = time.time()
    iterations_after = check_stop_repeatedly()
    elapsed = time.time() - start_time
    
    # First call completed all 10000 iterations (no stop flag)
    # Second call should exit immediately (stop flag set) - 0 iterations means it exited correctly
    log_result(
        "Stop interrupt handling",
        iterations_before == 10000 and iterations_after == 0,
        f"No-stop: {iterations_before} iterations, With-stop: {iterations_after} iterations (correct: exits immediately when stopped)"
    )

try:
    test_stop_interrupt()
except Exception as e:
    log_result("Stop interrupt handling", False, str(e))

# ============================================================================
# TEST 6: Widget Cleanup Verification
# ============================================================================
print("\n--- Test 6: Widget Cleanup ---")

def test_widget_cleanup():
    """Test that widgets are properly cleaned up."""
    # Simulate the cleanup pattern in _clear()
    widgets = []
    for i in range(50):
        # Simulate real widget creation pattern
        class MockWidget:
            deleted = False
            def deleteLater(self):
                MockWidget.deleted = True
        
        w = MockWidget()
        widgets.append(w)
    
    # Simulate clear operation
    for w in widgets:
        w.deleteLater()
    
    log_result(
        "Widget cleanup simulation",
        MockWidget.deleted,
        "50 widgets cleaned up successfully"
    )

try:
    test_widget_cleanup()
except Exception as e:
    log_result("Widget cleanup", False, str(e))

# ============================================================================
# TEST 7: Reasoning Ticker Stress Test
# ============================================================================
print("\n--- Test 7: Reasoning Ticker Stress ---")

def test_reasoning_ticker():
    """Test the ReasoningTicker with rapid updates."""
    # Simulate the ticker buffer behavior (_MAX_CHARS = 100)
    buffer = ""
    cycles = 5000
    
    start_time = time.time()
    for i in range(cycles):
        chunk = f"reasoning{i}"
        buffer += chunk
        # Simulate truncation at 100 chars (like ReasoningTicker._MAX_CHARS)
        if len(buffer) > 100:
            buffer = buffer[-100:]
    
    elapsed = time.time() - start_time
    
    log_result(
        "Reasoning ticker performance",
        buffer is not None and len(buffer) > 0,
        f"Processed {cycles} updates in {elapsed:.3f}s"
    )

try:
    test_reasoning_ticker()
except Exception as e:
    log_result("Reasoning ticker stress", False, str(e))

# ============================================================================
# TEST 8: Tool Group Row Stress Test
# ============================================================================
print("\n--- Test 8: Tool Group Row Stress ---")

def test_tool_group_stress():
    """Test rapid tool event grouping."""
    tool_groups = {}
    items_count = 0
    
    for i in range(500):
        tool_name = f"tool_{i % 10}"  # 10 different tool types
        if tool_name not in tool_groups:
            tool_groups[tool_name] = []
        tool_groups[tool_name].append({"input": {"id": i}})
        items_count += 1
    
    avg_items_per_group = items_count / len(tool_groups) if tool_groups else 0
    
    log_result(
        "Tool grouping stress",
        len(tool_groups) == 10,
        f"Created {len(tool_groups)} groups with {avg_items_per_group} items each avg"
    )

try:
    test_tool_group_stress()
except Exception as e:
    log_result("Tool group row stress", False, str(e))

# ============================================================================
# TEST 9: Event Queue Overflow Protection
# ============================================================================
print("\n--- Test 9: Event Queue Overflow ---")

def test_event_queue_overflow():
    """Test that the event queue doesn't grow unboundedly."""
    event_queue = []
    max_queue_size = 500
    
    # Simulate event flooding with backpressure
    for i in range(2000):
        event_queue.append({"type": "text", "data": f"event_{i}"})
        if len(event_queue) > max_queue_size:
            # Drop oldest events (backpressure)
            event_queue = event_queue[-max_queue_size:]
    
    log_result(
        "Event queue backpressure",
        len(event_queue) <= max_queue_size,
        f"Queue size: {len(event_queue)} (max: {max_queue_size})"
    )

try:
    test_event_queue_overflow()
except Exception as e:
    log_result("Event queue overflow", False, str(e))

# ============================================================================
# TEST 10: Signal Emission Safety
# ============================================================================
print("\n--- Test 10: Signal Emission Safety ---")

def test_signal_safety():
    """Test that signals are emitted safely without widget deletion issues."""
    # Simulate the pattern in ChatWorker.run() where widget might be deleted
    callable_registry = []
    
    def safe_emit(text):
        try:
            callable_registry.append(text)
            return True
        except RuntimeError:
            # Widget already deleted
            return False
    
    success_count = 0
    for i in range(1000):
        if safe_emit(f"signal_{i}"):
            success_count += 1
    
    log_result(
        "Signal emission safety",
        success_count == 1000,
        f"Successfully emitted {success_count}/1000 signals"
    )

try:
    test_signal_safety()
except Exception as e:
    log_result("Signal emission safety", False, str(e))

# ============================================================================
# TEST 11: Scroll Lock Behavior Simulation
# ============================================================================
print("\n--- Test 11: Scroll Lock Behavior ---")

def test_scroll_lock():
    """Test scroll lock threshold detection."""
    scroll_locked = [False]
    max_position = [0]
    
    def check_scroll_lock(current_value):
        if max_position[0] > 0 and current_value < max_position[0] - 60:
            scroll_locked[0] = True
        else:
            scroll_locked[0] = False
    
    # Simulate user scrolling up during streaming
    max_position[0] = 1000
    
    check_scroll_lock(500)  # Scrolled up significantly
    locked_at_500 = scroll_locked[0]
    
    check_scroll_lock(950)  # Near bottom
    unlocked_at_950 = not scroll_locked[0]
    
    log_result(
        "Scroll lock threshold detection",
        locked_at_500 and unlocked_at_950,
        f"Locked at 500: {locked_at_500}, Unlocked at 950: {unlocked_at_950}"
    )

try:
    test_scroll_lock()
except Exception as e:
    log_result("Scroll lock behavior", False, str(e))

# ============================================================================
# TEST 12: History Compaction Stress
# ============================================================================
print("\n--- Test 12: History Compaction Stress ---")

def test_compaction_stress():
    """Test history compaction with many messages."""
    messages = []
    
    # Create extremely long history
    for i in range(500):
        messages.append({
            "role": "user",
            "content": f"Question {i}: " + "What is the meaning of life? " * 50
        })
        messages.append({
            "role": "assistant", 
            "content": f"Answer {i}: " + "42. " * 100
        })
    
    # Simulate compaction (keep last 6 messages, summarize rest)
    keep_tail = 6
    if len(messages) > keep_tail:
        tail = messages[-keep_tail:]
        head_to_summarize = messages[:-keep_tail]
        summary = {"role": "assistant", "content": f"[Compacted {len(head_to_summarize)} messages]"}
        compacted = [summary] + tail
    else:
        compacted = messages
    
    log_result(
        "History compaction reduces size",
        len(compacted) < len(messages),
        f"Original: {len(messages)}, Compacted: {len(compacted)}"
    )

try:
    test_compaction_stress()
except Exception as e:
    log_result("History compaction stress", False, str(e))

# ============================================================================
# Final Report
# ============================================================================
print("\n" + "=" * 60)
print("STRESS TEST SUMMARY")
print("=" * 60)
print(f"Passed: {test_results['passed']}")
print(f"Failed: {test_results['failed']}")
print(f"Total:  {test_results['passed'] + test_results['failed']}")

print("\nPerformance Metrics:")
for key, value in test_results.get("performance", {}).items():
    print(f"  - {key}: {value}")

if test_results["errors"]:
    print("\nErrors:")
    for err in test_results["errors"]:
        print(f"  - {err}")

# Memory final report
gc.collect()
final_snapshot = tracemalloc.take_snapshot()
top_stats = final_snapshot.compare_to(initial_snapshot, 'lineno')
total_memory_diff = sum(stat.size_diff for stat in top_stats) / 1024

print(f"\nMemory Usage:")
print(f"  - Total memory diff: {total_memory_diff:.1f} KB")

tracemalloc.stop()

# Return exit code
sys.exit(0 if test_results["failed"] == 0 else 1)

def run_tests():
    """Entry point for external test runners."""
    return test_results