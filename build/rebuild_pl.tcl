# Vivado2025.2: vivado -mode batch -source build/rebuild_pl.tcl -tclargs NEW_OUTPUT_DIR
if {$argc != 1} {error "Expected a NEW output directory"}
set base [file normalize [file join [file dirname [info script]] ..]]
set out [file normalize [lindex $argv 0]]
if {[file exists $out]} {error "Output already exists; refuse overwrite"}
set_param general.maxThreads 4
create_project stream_subsystem [file join $out project] -part xc7z020clg400-2
add_files [glob [file join $base src pl rtl *.v]]
add_files [file join $base src pl log2_lut.hex]
source [file join $base build ps_stream_recreate.tcl]
set bd [get_files -quiet */ps_stream.bd]
if {[llength $bd] != 1} {error "Missing ps_stream BD"}
generate_target all $bd
set wrappers [make_wrapper -files $bd -top]
add_files -norecurse $wrappers
add_files -fileset constrs_1 [glob [file join $base build constr *.xdc]]
set_property top ps_stream_wrapper [current_fileset]
update_compile_order -fileset sources_1
if {[info exists ::env(BCI_PROJECT_ONLY)]} {save_project_as prepared [file join $out prepared]; close_project; exit}
launch_runs impl_1 -to_step write_bitstream -jobs 1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {error "Implementation did not finish"}
open_run impl_1
report_timing_summary -file [file join $out timing.rpt]
report_utilization -file [file join $out utilization.rpt]
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$wns < 0 || $whs < 0} {error "Timing failed: WNS=$wns WHS=$whs"}
file copy [get_property DIRECTORY [get_runs impl_1]]/ps_stream_wrapper.bit [file join $out bci_platform.bit]
close_project
