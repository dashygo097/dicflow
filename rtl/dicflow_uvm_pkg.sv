package dicflow_uvm_pkg;
  import uvm_pkg::*;

  // Call after run_test() returns with finish_on_completion disabled. The
  // environment owns its scoreboard checks and unfinished-transaction checks.
  task automatic finish_uvm(input int unsigned checks);
    uvm_report_server server;
    server = uvm_report_server::get_server();
    if (server.get_severity_count(UVM_ERROR) != 0 ||
        server.get_severity_count(UVM_FATAL) != 0) begin
      $fatal(1, "UVM completed with errors or fatals");
    end
    dicflow_verify_pkg::pass_test(checks);
  endtask
endpackage
