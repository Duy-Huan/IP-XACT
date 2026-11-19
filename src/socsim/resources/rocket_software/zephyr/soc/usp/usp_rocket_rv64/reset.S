/* SPDX-License-Identifier: Apache-2.0 */
.section .usp_reset,"ax"
.global usp_reset
.option norvc
usp_reset:
    j __start
