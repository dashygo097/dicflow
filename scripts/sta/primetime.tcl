# One isolated PrimeTime process per phase/mode/library/RC view.
proc sta_require_file {path} {
  if {![file isfile $path]} { error "missing STA input: $path" }
}

proc sta_json_string {value} {
  return "\"[string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $value]\""
}

proc sta_write_summary {analysis timing metrics {message ""}} {
  global STA
  set output [open [file join $::env(STAGE_REPORT_DIR) summary.json] w]
  puts $output "\{"
  foreach key {phase view mode top clock_model interconnect_model rc} {
    puts $output "  [sta_json_string $key]: [sta_json_string [dict get $STA $key]],"
  }
  puts $output "  \"analysis_status\": [sta_json_string $analysis],"
  puts $output "  \"timing_status\": [sta_json_string $timing],"
  puts $output "  \"message\": [sta_json_string $message],"
  set entries {}
  dict for {check values} $metrics {
    set fields {}
    dict for {key value} $values { lappend fields "[sta_json_string $key]: $value" }
    lappend entries "[sta_json_string $check]: \{[join $fields {, }]\}"
  }
  puts $output "  \"metrics\": \{[join $entries {, }]\}"
  puts $output "\}"
  close $output
  set output [open [file join $::env(STAGE_REPORT_DIR) summary.rpt] w]
  puts $output "STA [dict get $STA phase] / [dict get $STA view] / [dict get $STA mode]"
  puts $output "Analysis: $analysis; timing: $timing"
  puts $output "Interconnect: [dict get $STA interconnect_model]; clocks: [dict get $STA clock_model]"
  puts $output "Timing values use native library time units (see units.rpt)."
  dict for {check values} $metrics { puts $output "$check: $values" }
  if {$message ne ""} { puts $output $message }
  close $output
}

proc sta_metrics {check limit} {
  set delay [expr {$check eq "setup" ? "max" : "min"}]
  set worst [get_timing_paths -delay_type $delay -max_paths 1 -nworst 1]
  if {[sizeof_collection $worst] == 0} { error "no constrained $check timing paths" }
  set slack [get_attribute [index_collection $worst 0] slack]
  if {![string is double -strict $slack] || abs($slack) == Inf} {
    error "invalid or unconstrained $check slack: $slack"
  }
  set paths [get_timing_paths -delay_type $delay -slack_lesser_than 0.0 -max_paths $limit -nworst 1]
  if {[sizeof_collection $paths] >= $limit} {
    error "$check violation count reached metrics_limit=$limit; refusing truncated metrics"
  }
  # PT returns paths per endpoint/path group. Count each endpoint once globally.
  set endpoints [dict create]
  foreach_in_collection path $paths {
    set value [get_attribute $path slack]
    set endpoint [get_object_name [get_attribute $path endpoint]]
    if {![dict exists $endpoints $endpoint] || $value < [dict get $endpoints $endpoint]} {
      dict set endpoints $endpoint $value
    }
  }
  set tns 0.0
  dict for {endpoint value} $endpoints { set tns [expr {$tns + $value}] }
  return [dict create worst_slack $slack wns [expr {min(0.0, $slack)}] tns $tns violating_endpoints [dict size $endpoints]]
}

proc sta_check_parasitics {allowed} {
  set missing {}
  foreach_in_collection net [get_nets -hierarchical *] {
    # Empty and ideal nets do not need an extracted timing network.
    if {[get_attribute $net is_ideal]} { continue }
    if {[sizeof_collection [get_attribute $net leaf_drivers]] == 0 ||
        [sizeof_collection [get_attribute $net leaf_loads]] == 0} { continue }
    if {[get_attribute $net has_valid_parasitics]} { continue }
    set name [get_object_name $net]
    set waived false
    foreach pattern $allowed {
      if {[string match $pattern $name]} { set waived true; break }
    }
    if {!$waived} { lappend missing $name }
  }
  if {[llength $missing]} {
    error "missing/invalid parasitics on [llength $missing] connected nets: [lrange $missing 0 19]"
  }
}

proc sta_main {} {
  global STA
  foreach name {STAGE_CONFIG STAGE_INPUT_NETLIST STAGE_INPUT_CONSTRAINTS DEPENDENCY_TOP STAGE_REPORT_DIR BUILD_JOBS} {
    if {![info exists ::env($name)] || $::env($name) eq ""} {
      error "missing environment variable: $name"
    }
  }
  source $::env(STAGE_CONFIG)
  if {[dict get $STA top] ne $::env(DEPENDENCY_TOP)} {
    error "upstream and STA design top differ"
  }
  set initial_errors [get_message_info -error_count]
  set_app_var sh_continue_on_error false
  set_app_var link_create_black_boxes false
  set_app_var timing_report_unconstrained_paths false
  set_host_options -max_cores $::env(BUILD_JOBS)
  foreach library [dict get $STA libraries] { sta_require_file $library }
  set paths {}
  foreach library [dict get $STA libraries] { lappend paths [file dirname $library] }
  set_app_var search_path [concat [get_app_var search_path] $paths]
  set_app_var link_path [concat [list *] [dict get $STA libraries]]
  dict for {name value} [dict get $STA app_vars] { set_app_var $name $value }
  sta_require_file $::env(STAGE_INPUT_NETLIST)
  sta_require_file $::env(STAGE_INPUT_CONSTRAINTS)
  read_verilog $::env(STAGE_INPUT_NETLIST)
  if {![link_design [dict get $STA top]]} { error "failed to link STA netlist" }
  read_sdc $::env(STAGE_INPUT_CONSTRAINTS)
  # Technology/view/mode hooks run after link+SDC, before clock and RC policy.
  foreach script [dict get $STA setup_scripts] {
    sta_require_file $script
    uplevel #0 [list source $script]
  }
  dict for {name value} [dict get $STA case_analysis] {
    set objects [get_ports -quiet $name]
    if {[sizeof_collection $objects] == 0} { set objects [get_pins -quiet -hierarchical $name] }
    if {[sizeof_collection $objects] == 0} { error "case-analysis object not found: $name" }
    set_case_analysis $value $objects
  }
  set clocks [get_clocks -quiet *]
  if {[sizeof_collection $clocks] == 0} { error "STA has no clocks" }
  foreach_in_collection clock $clocks {
    if {[dict get $STA clock_model] eq "ideal"} {
      remove_propagated_clock $clock
    } elseif {[sizeof_collection [get_attribute $clock sources]] > 0} {
      # Drop estimated network latency; preserve source latency and virtual clocks.
      remove_clock_latency $clock
      set_propagated_clock $clock
    }
  }
  set reports $::env(STAGE_REPORT_DIR)
  set_app_var auto_wire_load_selection false
  if {[dict get $STA interconnect_model] eq "extracted"} {
    if {![info exists ::env(STAGE_INPUT_PARASITICS)] || $::env(STAGE_INPUT_PARASITICS) eq ""} {
      error "postlayout requires the selected RC parasitics artifact"
    }
    sta_require_file $::env(STAGE_INPUT_PARASITICS)
    remove_wire_load_model [get_designs *]
    read_parasitics -format SPEF $::env(STAGE_INPUT_PARASITICS)
    redirect -file [file join $reports parasitics.rpt] {
      report_annotated_parasitics -check -list_not_annotated
    }
    sta_check_parasitics [dict get $STA allowed_unannotated_nets]
  } else {
    set_wire_load_mode [dict get $STA wire_load_mode]
    set_wire_load_model -name [dict get $STA wire_load_model] [current_design]
  }
  update_timing -full
  # check_timing's return value is not a pass/fail Boolean. Native warnings from
  # these structural checks are fatal; reports retain the detailed diagnostics.
  set warnings [get_message_info -warning_count]
  redirect -file [file join $reports check_timing.rpt] {
    check_timing -verbose -override_defaults {no_clock unconstrained_endpoints loops generated_clocks}
  }
  if {[get_message_info -warning_count] > $warnings} {
    error "STA constraint integrity check failed; see check_timing.rpt"
  }
  redirect -file [file join $reports constraints.rpt] { report_constraint -all_violators }
  redirect -file [file join $reports coverage.rpt] { report_analysis_coverage -status_details {untested violated} }
  redirect -file [file join $reports clocks.rpt] { report_clock -attributes }
  redirect -file [file join $reports units.rpt] { report_units }
  set metrics [dict create]
  set violated false
  foreach check [dict get $STA checks] {
    set delay [expr {$check eq "setup" ? "max" : "min"}]
    redirect -file [file join $reports timing.${check}.rpt] {
      report_timing -delay_type $delay -max_paths [dict get $STA report_paths] -transition_time -capacitance -nets -input_pins
    }
    set values [sta_metrics $check [dict get $STA metrics_limit]]
    dict set metrics $check $values
    if {[dict get $values wns] < 0} { set violated true }
  }
  if {[get_message_info -error_count] > $initial_errors} {
    error "PrimeTime reported errors; see execution log"
  }
  set timing [expr {$violated ? "violated" : "met"}]
  sta_write_summary complete $timing $metrics
  puts "STA [dict get $STA phase]/[dict get $STA view]: $timing"
  if {$violated && [dict get $STA fail_on_violations]} {
    return -code error -errorcode STA_VIOLATION "STA timing violations; see summary.json"
  }
}

if {[catch {sta_main} message options]} {
  # Retain completed metrics when the timing gate fails. Setup/analysis failures
  # have a separate status and can never be mistaken for a timing result.
  if {![info exists STA]} {
    set STA [dict create phase unknown view unknown mode unknown top unknown clock_model unknown interconnect_model unknown rc unknown]
  }
  if {[dict get $options -errorcode] ne "STA_VIOLATION"} {
    sta_write_summary error unknown {} $message
  }
  puts stderr "ERROR: $message"
  puts stderr [dict get $options -errorinfo]
  exit 1
}
exit 0
