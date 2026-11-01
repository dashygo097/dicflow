package dicflow_verify_pkg;
  task automatic check(input logic condition, input string message);
    if (condition !== 1'b1) begin
      $fatal(1, "%s", message);
    end
  endtask

  task automatic pass_test(input int unsigned checks);
    if (checks == 0) begin
      $fatal(1, "Verification completed without checks");
    end
    $display("DICFLOW_VERIFY_PASS checks=%0d", checks);
    $finish;
  endtask
endpackage
