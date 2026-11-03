package df_verify_pkg;
  class test_state;
    local static longint unsigned checks_r = 0;
    local static bit finished_r = 1'b0;

    static function longint unsigned get_check_count();
      return checks_r;
    endfunction

    static function void record_check();
      if (finished_r) $fatal(1, "Check attempted after test completion");
      checks_r++;
    endfunction

    static function void complete(longint unsigned checks);
      if (finished_r) $fatal(1, "Test completion reported more than once");
      if (checks == 0) $fatal(1, "Verification completed without checks");
      finished_r = 1'b1;
      $display("DICFLOW_VERIFY_PASS checks=%0d", checks);
    endfunction
  endclass

  function automatic void check(input logic condition, input string message = "Check failed");
    test_state::record_check();
    if (condition !== 1'b1) $fatal(1, "%s", message);
  endfunction

  function automatic longint unsigned get_check_count();
    return test_state::get_check_count();
  endfunction

  // Compatibility with external checkers, including the UVM result adapter.
  task automatic pass_test(input longint unsigned checks);
    test_state::complete(checks);
    $finish;
  endtask

  // Normal plain-SV testbenches do not need a separate checks++ counter.
  task automatic finish_test();
    pass_test(get_check_count());
  endtask

  // Call with a logic clock and signals driven away from its rising edge.
  task automatic wait_cycles(ref logic clk, input int unsigned cycles);
    repeat (cycles) @(posedge clk);
  endtask

  // Sample at most max_cycles rising edges. A successful bounded wait is a check.
  task automatic wait_for_value(ref logic clk, ref logic value, input logic expected,
                                input int unsigned max_cycles,
                                input string message = "Timed out waiting for signal");
    if (max_cycles == 0) $fatal(1, "wait_for_value requires max_cycles > 0");
    repeat (max_cycles) begin
      @(posedge clk);
      if (value === expected) begin
        check(1'b1, message);
        return;
      end
    end
    check(1'b0, $sformatf(
          "%s: expected=%b actual=%b after %0d cycles", message, expected, value, max_cycles));
  endtask

  // Parameterization preserves the caller's width, signedness and four-state
  // representation. Intended for packed hardware values, enums and structs.
  class value_check #(
      type value_t
  );
    static function void equal(input value_t actual, input value_t expected,
                               input string message = "Value mismatch");
      check(actual === expected, $sformatf("%s: expected=%p actual=%p", message, expected, actual));
    endfunction

    static function void known(input value_t value, input string message = "Unknown value");
      check(!$isunknown(value), $sformatf("%s: actual=%p", message, value));
    endfunction

    static function void onehot(input value_t value, input string message = "Invalid one-hot value",
                                input bit allow_zero = 1'b0);
      check(!$isunknown(value) && (allow_zero ? $onehot0(value) : $onehot(value)), $sformatf(
            "%s: actual=%p allow_zero=%b", message, value, allow_zero));
    endfunction
  endclass

  // Generic in-order comparison storage. Enqueue only accepted input/expected
  // transactions and check only accepted output transactions. Protocol timing,
  // capacity and reset policy remain the responsibility of the testbench.
  class ordered_scoreboard #(
      type value_t
  );
    local value_t expected[$];
    local longint unsigned comparisons_r = 0;
    local string name;

    function new(string name = "scoreboard");
      this.name = name;
    endfunction

    function void push_expected(input value_t value);
      expected.push_back(value);
    endfunction

    function void check_actual(input value_t actual);
      value_t expected_value;
      check(expected.size() != 0, $sformatf(
            "%s: unexpected output actual=%p with no pending expectation", name, actual));
      expected_value = expected.pop_front();
      value_check#(value_t)::equal(actual, expected_value, $sformatf(
                                   "%s: output mismatch pending=%0d", name, expected.size()));
      comparisons_r++;
    endfunction

    function int unsigned pending_count();
      return expected.size();
    endfunction

    function longint unsigned comparison_count();
      return comparisons_r;
    endfunction

    function void check_empty();
      check(expected.size() == 0, $sformatf(
            "%s: %0d outstanding expectations", name, expected.size()));
    endfunction

    // A reset may intentionally discard pending data; retain lifetime statistics.
    function void clear();
      expected.delete();
    endfunction
  endclass
endpackage
