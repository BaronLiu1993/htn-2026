export type Account = { id: string; name: string; state: string; tiv: number; premium: number; year: number; group: string; status: string; reason: string; action: string; targets: boolean[] };
export const accounts: Account[] = [
 {id:'A',name:'Oak & Iron Manufacturing',state:'OH',tiv:80,premium:90,year:2018,group:'Pursue',status:'Target',reason:'All four target preferences match. Required evidence is complete.',action:'Start underwriting review',targets:[true,true,true,true]},
 {id:'B',name:'Summit Industrial Partners',state:'OH',tiv:120,premium:140,year:2018,group:'Pursue',status:'Acceptable',reason:'Target state and building age. Value and premium remain acceptable.',action:'Review acceptable opportunity',targets:[true,false,false,true]},
 {id:'C',name:'James River Properties',state:'VA',tiv:80,premium:90,year:2005,group:'Pursue',status:'Acceptable',reason:'Target value and premium. State and building age remain acceptable.',action:'Review acceptable opportunity',targets:[false,true,true,false]},
 {id:'E',name:'Northline Logistics',state:'OH',tiv:80,premium:90,year:2018,group:'Investigate',status:'Needs review',reason:'Five-year loss history is missing. Eligibility is not established.',action:'Prepare loss-history request',targets:[true,true,true,true]},
 {id:'D',name:'Westhaven Commerce',state:'OH',tiv:80,premium:200,year:2018,group:'Out of appetite',status:'Out of appetite',reason:'$200K premium exceeds the $175K appetite maximum.',action:'Review exclusion',targets:[true,true,false,true]},
];
export const groups = ['Pursue','Investigate','Out of appetite'];
